"""Anthropic Claude LLM adapter (anthropic 1.x ``AsyncAnthropic``; streamed Messages API).

* Every request streams (``client.beta.messages.stream`` + ``get_final_message``) so the large
  ``max_tokens`` budgets of thinking models never hit HTTP timeouts. Each streamed request is
  capped by ``ANTHROPIC_TIMEOUT_SECONDS`` (not ``LLM_TIMEOUT_SECONDS``): at high effort with a 64K
  token budget an answer can legitimately take longer than ten minutes.
* A stalled request is retried early: the response headers must arrive within
  ``LLM_FIRST_RESPONSE_TIMEOUT_SECONDS`` (plus an upload allowance for large documents), and the
  streamed body may not be silent longer than ``LLM_IDLE_TIMEOUT_SECONDS`` (the client's HTTP read
  timeout; the API's keep-alive pings during long thinking count as data).
* JSON comes from structured outputs (``output_config.format``). A schema the API refuses to
  compile (too complex) is sent in the prompt instead, remembered per schema name.
* Current models think adaptively by default and reject sampling parameters, so neither
  ``thinking`` nor ``temperature`` is sent; depth is set with ``output_config.effort``.
* The system prompt, the attached files and the whole first user turn are prompt-cache
  breakpoints: every scene of a lecture shares the system prompt and the source PDF, and
  validation re-asks reuse the entire first turn.
* A safety-classifier refusal is re-run server-side on another model (``fallbacks="default"``)
  where the model supports it; a final refusal raises ``ContentBlocked``. Every attempt of such a
  chain is billed, so usage is reported per attempt (``usage.iterations``), each at its own model.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Sequence
from typing import Any

from ...config import Settings
from .._common import make_error, safe_attr
from .._notify import hold_waiting, release_waiting
from .._retry import SleepFn
from .._stream import StallGuard, first_response_limit, payload_size
from ..anthropic_common import AnthropicCaller, ClientFactory
from ..base import FileInput, ImageInput, ProviderError, SemanticValidator, T, Usage, UsageSink
from .schema import schema_name, to_provider_schema
from .structured import Completion, Turn, run_structured, settle

log = logging.getLogger(__name__)

_TEXT_MIMES = ("text/", "application/json", "application/xml")
#: Encoded-size budget for one request (the API rejects bodies over 32 MB; base64 adds a third).
MAX_REQUEST_BYTES = 30_000_000
MAX_TOKENS_CAP = 128_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
JSON_PROMPT = "Reply with only one JSON object that matches this JSON schema:\n"
_CACHE = {"type": "ephemeral"}
# Models with output_config.effort (prefix match); Haiku, Sonnet 4.5 and Claude 3 reject it.
_EFFORT_MODELS = (
    "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
    "claude-sonnet-4-6", "claude-sonnet-5", "claude-fable-5", "claude-mythos",
)
_NO_XHIGH = ("claude-opus-4-5", "claude-opus-4-6", "claude-sonnet-4-6")
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")
_SCHEMA_WORDS = ("schema", "output_config", "format", "too complex", "grammar")
# usage.iterations entries that are model attempts (compaction / advisor entries are never enabled here).
_HOP_TYPES = ("message", "fallback_message")


def resolve_model(model: str, settings: Settings) -> str:
    """Map a non-Claude model name (e.g. a Gemini default) onto the configured Claude models."""
    if model.lower().startswith("claude"):
        return model
    mapped = settings.anthropic_model_plan if "pro" in model.lower() else settings.anthropic_model_fast
    log.warning("anthropic provider got non-Claude model %s; using %s (set ANTHROPIC_MODEL_* for Claude)",
                model, mapped)
    return mapped


def effort_for(model: str, effort: str) -> str | None:
    """``output_config.effort`` for ``model`` (``None`` when the model has no effort control)."""
    m = model.lower()
    if not m.startswith(_EFFORT_MODELS):
        return None
    if effort == "xhigh" and m.startswith(_NO_XHIGH):
        return "high"
    if effort == "max" and m.startswith("claude-opus-4-5"):
        return "high"
    return effort


def supports_fallbacks(model: str) -> bool:
    """Whether ``model`` accepts server-side refusal fallbacks (``fallbacks="default"``)."""
    return model.lower().startswith(_FALLBACK_MODELS)


def max_tokens_for(settings: Settings, max_output_tokens: int | None) -> int:
    """Output budget: thinking counts toward ``max_tokens``, and callers' limits were tuned for
    non-thinking models, so the configured budget is a floor."""
    return min(max(int(settings.anthropic_max_tokens), int(max_output_tokens or 0)), MAX_TOKENS_CAP)


def _hop_usage(u: Any, *, model: str, operation: str, requested: str, declined: bool = False) -> Usage:
    """``Usage`` of one attempt (top-level ``usage`` or one ``usage.iterations`` entry).

    Cache writes and reads are part of the input (``meta`` lets pricing bill them at their own
    rates); ``requested_model`` marks an attempt served by another model, ``declined`` an attempt
    whose answer was refused and re-run on a fallback model.
    """
    write = int(safe_attr(u, "cache_creation_input_tokens", default=0) or 0)
    read = int(safe_attr(u, "cache_read_input_tokens", default=0) or 0)
    meta = {"cache_read_tokens": read, "cache_write_tokens": write,
            "requested_model": requested if model != requested else None, "declined": declined}
    return Usage(
        provider="anthropic", model=model, operation=operation,
        input_tokens=int(safe_attr(u, "input_tokens", default=0) or 0) + write + read,
        output_tokens=int(safe_attr(u, "output_tokens", default=0) or 0),
        meta={k: v for k, v in meta.items() if v},
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _b64_async(data: bytes) -> str:
    """Base64 text; large payloads are encoded in a worker thread (CPU-bound)."""
    if len(data) > 1_000_000:
        return await asyncio.to_thread(_b64, data)
    return _b64(data)


def _b64_size(n: int) -> int:
    return 4 * ((max(0, n) + 2) // 3)


class AnthropicLLM:
    """``LLMProvider`` backed by ``client.beta.messages.stream`` (``output_config.format`` = ``json_schema``)."""

    name = "anthropic"

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
    ) -> None:
        self.settings = settings
        self._caller = AnthropicCaller(
            settings, timeout_s=settings.anthropic_timeout_seconds, client_factory=client_factory, sleep=sleep,
            max_attempts=max_attempts, idle_timeout_s=settings.llm_idle_timeout_seconds,
        )
        self._prompt_mode: set[str] = set()  # schemas the API refused to compile (prompt mode worked)
        self._no_fallback: set[str] = set()  # models that rejected the fallbacks parameter

    # --- request building --------------------------------------------------------------------
    def _is_text(self, f: FileInput) -> bool:
        return f.mime.startswith(_TEXT_MIMES) or f.mime == "text/markdown"

    async def _user_content(self, *, system: str, prompt: str, files: Sequence[FileInput],
                            images: Sequence[ImageInput]) -> list[dict[str, Any]]:
        """First-turn content blocks: documents, images, then the prompt (built once per call)."""
        size = len(system.encode("utf-8")) + len(prompt.encode("utf-8"))
        for f in files:
            if not self._is_text(f) and f.mime != "application/pdf":
                raise make_error(ProviderError, self.name, f"unsupported file type {f.mime} (only PDF and text)",
                                 settings=self.settings)
            size += len(f.data) if self._is_text(f) else _b64_size(len(f.data))
        size += sum(_b64_size(len(im.data)) for im in images)
        if size > MAX_REQUEST_BYTES:
            raise make_error(ProviderError, self.name,
                             f"request too large: about {size / 1e6:.1f} MB once encoded, the Claude API accepts "
                             "at most 32 MB; use a smaller document", settings=self.settings)
        content: list[dict[str, Any]] = []
        for f in files:
            if self._is_text(f):
                text = f"[{f.name or 'document'}]\n" + f.data.decode("utf-8", "replace")
                content.append({"type": "text", "text": text})
            else:
                data = await _b64_async(f.data)
                content.append({"type": "document",
                                "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
        for im in images:
            data = await _b64_async(im.data)
            content.append({"type": "image", "source": {"type": "base64", "media_type": im.mime, "data": data}})
        content.append({"type": "text", "text": prompt})
        return content

    def _request(self, *, model: str, system: str, content: list[dict[str, Any]], followups: list[Turn],
                 max_output_tokens: int | None, json_schema: dict[str, Any] | None,
                 prompt_mode: bool = False) -> dict[str, Any]:
        """Keyword arguments of ``client.beta.messages.stream`` (``prompt_mode``: schema in the prompt)."""
        prompt_mode = prompt_mode and json_schema is not None
        first = list(content)
        if prompt_mode:
            schema_text = json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))
            first[-1] = {"type": "text", "text": f"{first[-1]['text']}\n\n{JSON_PROMPT}{schema_text}"}
        # Cache breakpoints (at most 4 per request): system, the attachments shared by every scene of
        # a lecture, and the whole first turn (validation re-asks resend it unchanged).
        if len(first) > 1:
            first[-2] = {**first[-2], "cache_control": _CACHE}
        first[-1] = {**first[-1], "cache_control": _CACHE}
        messages: list[dict[str, Any]] = [{"role": "user", "content": first}]
        for t in followups:
            messages.append({"role": "assistant" if t.role == "model" else "user",
                             "content": t.text or "(empty response)"})
        while messages[-1]["role"] == "assistant":  # a trailing assistant turn is a prefill: rejected (400)
            messages.pop()
        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens_for(self.settings, max_output_tokens),
                                  "messages": messages}
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": _CACHE}]
        output_config: dict[str, Any] = {}
        effort = effort_for(model, self.settings.anthropic_effort)
        if effort:
            output_config["effort"] = effort
        if json_schema is not None and not prompt_mode:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        if self.settings.anthropic_refusal_fallback and supports_fallbacks(model) and model not in self._no_fallback:
            # A safety-classifier decline is re-run server-side on the model Anthropic recommends for
            # that refusal category (science lectures can trip classifier false positives).
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _rejected_feature(self, exc: BaseException, kwargs: dict[str, Any], *, name: str) -> str | None:
        """Request feature a 400 complains about (``"fallbacks"`` | ``"schema"``), or ``None`` to give up."""
        text = " ".join(str(getattr(exc, "message", "") or exc).split())
        lower = text.lower()
        detail = self.settings.redact(text)[:300]
        model = kwargs["model"]
        if "fallbacks" in kwargs and "fallback" in lower:
            log.warning("anthropic: %s rejected refusal fallbacks; retrying without them (%s)", model, detail)
            return "fallbacks"
        has_format = "format" in (kwargs.get("output_config") or {})
        if has_format and "effort" not in lower and any(w in lower for w in _SCHEMA_WORDS):
            log.warning("anthropic: %s structured output rejected for %s; retrying with the schema in the prompt "
                        "(%s)", model, name, detail)
            return "schema"
        return None

    # --- response handling -------------------------------------------------------------------
    def _usages(self, message: Any, *, requested: str, operation: str) -> tuple[Usage, list[Usage]]:
        """``(usage of the returned attempt, earlier billed attempts)`` of one response.

        With refusal fallbacks the top-level ``usage`` covers only the attempt that produced the
        message; ``usage.iterations`` lists every attempt (a declined hop as ``message``, the
        serving hop as ``fallback_message``), each billed at its own model's rates. A decline
        before any output may be credited by Anthropic; it is still counted (budgets err high).
        """
        served = str(safe_attr(message, "model", default="") or requested)
        hops = [e for e in safe_attr(message, "usage", "iterations", default=None) or []
                if safe_attr(e, "type", default="") in _HOP_TYPES]
        if len(hops) < 2:  # one attempt (possibly sticky-served by a fallback): top-level usage is the bill
            return _hop_usage(safe_attr(message, "usage"), model=served, operation=operation,
                              requested=requested), []
        last = len(hops) - 1
        usages = [
            _hop_usage(hop, model=str(safe_attr(hop, "model", default="") or (served if i == last else requested)),
                       operation=operation, requested=requested, declined=i < last)
            for i, hop in enumerate(hops)
        ]
        return usages[-1], usages[:-1]

    def _completion(self, message: Any, *, requested: str, operation: str) -> Completion:
        usage, earlier = self._usages(message, requested=requested, operation=operation)
        stop = str(safe_attr(message, "stop_reason", default="") or "")
        if stop == "refusal":  # checked before the content: a refused answer may be partial
            category = safe_attr(message, "stop_details", "category")
            return Completion(text="", usage=usage, extra_usage=earlier,
                              blocked="refused" + (f": {category}" if category else ""))
        text = "".join(str(getattr(b, "text", "") or "") for b in safe_attr(message, "content", default=[]) or []
                       if getattr(b, "type", "") == "text")  # thinking / fallback blocks are skipped
        return Completion(text=text, usage=usage, extra_usage=earlier,
                          truncated=stop in ("max_tokens", "model_context_window_exceeded"))

    async def _complete(self, *, model: str, system: str, content: list[dict[str, Any]], followups: list[Turn],
                        max_output_tokens: int | None, json_schema: dict[str, Any] | None, name: str,
                        operation: str, what: str) -> Completion:
        prompt_mode = name in self._prompt_mode  # kept across retry attempts of this call

        async def request(client: Any) -> Any:
            import anthropic  # loaded off-loop by AnthropicCaller.call

            nonlocal prompt_mode
            while True:  # each pass drops at most one rejected feature, so this ends
                kwargs = self._request(model=model, system=system, content=content, followups=followups,
                                       max_output_tokens=max_output_tokens, json_schema=json_schema,
                                       prompt_mode=prompt_mode)
                # Only the start is bounded here (the request is sent and answered on __aenter__); once
                # the answer streams, the HTTP read timeout watches for silence (pings count). Until
                # then "still waiting" notices are held back near the limit (a stall is a retry notice).
                first_s = first_response_limit(self.settings.llm_first_response_timeout_seconds,
                                               payload_size(kwargs))
                hold_waiting(first_s)
                try:
                    async with StallGuard(first_s=first_s, idle_s=None, on_alive=release_waiting) as guard:
                        async with client.beta.messages.stream(**kwargs) as stream:
                            guard.touch()
                            message = await stream.get_final_message()
                except anthropic.BadRequestError as exc:
                    feature = self._rejected_feature(exc, kwargs, name=name)
                    if feature == "fallbacks":
                        self._no_fallback.add(model)
                    elif feature == "schema":
                        prompt_mode = True
                    else:
                        raise
                    continue
                if prompt_mode and json_schema is not None:
                    self._prompt_mode.add(name)  # remembered only once the prompt mode has worked
                return message

        message = await self._caller.call(request, what=what)
        return self._completion(message, requested=model, operation=operation)

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
        """Structured generation (``output_config.format``) + validation/re-ask loop.

        ``temperature`` is accepted for the protocol but never sent: current Claude models (Opus 5.5,
        Sonnet 5.5, Fable 5.x, Opus 5, Opus 4.7/4.8) reject sampling parameters with HTTP 400.
        """
        json_schema = to_provider_schema(schema, "anthropic")
        name = schema_name(schema)
        model = resolve_model(model, self.settings)
        content: list[dict[str, Any]] | None = None

        async def complete(followups: list[Turn]) -> Completion:
            nonlocal content
            if content is None:
                content = await self._user_content(system=system, prompt=prompt, files=files, images=images)
            return await self._complete(model=model, system=system, content=content, followups=followups,
                                        max_output_tokens=max_output_tokens, json_schema=json_schema, name=name,
                                        operation="vision" if images else "llm",
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
        """Free-text generation (``temperature`` is ignored, see ``generate_json``)."""
        model = resolve_model(model, self.settings)
        content = await self._user_content(system=system, prompt=prompt, files=files, images=images)
        completion = await self._complete(model=model, system=system, content=content, followups=[],
                                          max_output_tokens=max_output_tokens, json_schema=None, name="text",
                                          operation="vision" if images else "llm", what="text generation")
        await settle(completion, on_usage=on_usage, provider=self.name, what="text generation",
                     settings=self.settings)
        if not completion.text.strip():
            raise make_error(ProviderError, self.name, "text generation returned no text", settings=self.settings)
        return completion.text
