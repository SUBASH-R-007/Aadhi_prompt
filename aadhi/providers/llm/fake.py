"""Deterministic offline LLM (tests, development, e2e).

* ``FakeLLM.register(schema_name, responder)`` scripts the answer for a response model (class
  name). The responder receives ``(prompt, schema)`` and returns a dict or a model instance. It may
  also accept any of the keyword arguments ``attempt`` (0 = first ask), ``problems`` (the problem
  list of the previous attempt), ``system``, ``model``, ``images`` and ``files``; the attempt is
  also available from ``current_attempt()``. Responders may be async.
* Without a responder the fake synthesises a schema-valid instance (``synth.candidates``):
  ``minimal`` first, then richer instances on each re-ask.
* The exact same parse/validate/semantic-check/re-ask loop as the real providers runs
  (``structured.run_structured``) and a ``Usage`` (provider ``"fake"``) is reported per call.
"""

from __future__ import annotations

import collections
import hashlib
import inspect
import json
import threading
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel

from ...config import Settings
from .._common import emit_usage
from ..base import FileInput, ImageInput, SemanticValidator, T, Usage, UsageSink
from . import synth
from .schema import to_provider_schema
from .structured import CURRENT_ATTEMPT, CURRENT_PROBLEMS, Completion, Turn, run_structured

Responder = Callable[..., Any]
TextResponder = Callable[..., Any]

_OPTIONAL_KWARGS = ("attempt", "problems", "system", "model", "images", "files")

#: ``FakeLLM.calls`` keeps only recent, clipped records: the instance lives for the whole process
#: (factory cache) and pipeline prompts embed up to 400k characters of source text. Tests that need
#: the full prompt register a responder, which receives it.
MAX_CALL_RECORDS = 100
MAX_RECORDED_CHARS = 2000


def current_attempt() -> int:
    """Zero-based attempt number of the structured call in progress (for responders)."""
    return CURRENT_ATTEMPT.get()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _accepted_kwargs(fn: Callable[..., Any]) -> set[str] | None:
    """Names of optional kwargs ``fn`` accepts (``None`` = accepts ``**kwargs``)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return set()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return {p.name for p in params if p.name in _OPTIONAL_KWARGS}


async def _call_responder(fn: Callable[..., Any], args: tuple[Any, ...], extras: dict[str, Any]) -> Any:
    accepted = _accepted_kwargs(fn)
    kwargs = extras if accepted is None else {k: v for k, v in extras.items() if k in accepted}
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


def _clip(text: str) -> str:
    return text if len(text) <= MAX_RECORDED_CHARS else text[:MAX_RECORDED_CHARS] + "..."


def _call_record(kind: str, *, model: str, prompt: str, system: str, attempt: int, images: int, files: int,
                 schema: str | None = None) -> dict[str, Any]:
    """Compact record of one call: clipped prompt/system plus the full prompt's length and hash."""
    record: dict[str, Any] = {"kind": kind, "model": model}
    if schema is not None:
        record["schema"] = schema
    record.update(
        prompt=_clip(prompt),
        prompt_chars=len(prompt),
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()[:16],
        system=_clip(system),
        attempt=attempt,
        images=images,
        files=files,
    )
    return record


def _to_json_text(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, str):
        return value  # raw text: lets tests script malformed output
    return json.dumps(value, ensure_ascii=False, default=str)


class FakeLLM:
    """Offline ``LLMProvider``. Scripted responders are shared by every instance (class-level)."""

    name = "fake"
    _responders: ClassVar[dict[str, Responder]] = {}
    _text_responders: ClassVar[dict[str, TextResponder]] = {}  # dict: avoids method binding
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        #: recent calls for test inspection: {kind, model, schema, prompt (clipped), prompt_chars,
        #: prompt_sha256, system (clipped), attempt, images, files}
        self.calls: collections.deque[dict[str, Any]] = collections.deque(maxlen=MAX_CALL_RECORDS)

    # --- scripting -----------------------------------------------------------------------
    @classmethod
    def register(cls, schema_name: str, responder: Responder) -> None:
        """Script the answer for response model ``schema_name`` (the model class name)."""
        with cls._lock:
            cls._responders[schema_name] = responder

    @classmethod
    def unregister(cls, schema_name: str) -> None:
        with cls._lock:
            cls._responders.pop(schema_name, None)

    @classmethod
    def register_text(cls, responder: TextResponder | None) -> None:
        """Script ``generate_text`` answers: ``responder(prompt, system)`` -> str (``None`` resets)."""
        with cls._lock:
            if responder is None:
                cls._text_responders.pop("text", None)
            else:
                cls._text_responders["text"] = responder

    @classmethod
    def clear_registry(cls) -> None:
        """Remove every scripted responder (test isolation)."""
        with cls._lock:
            cls._responders.clear()
            cls._text_responders.clear()

    @classmethod
    def registered(cls) -> list[str]:
        with cls._lock:
            return sorted(cls._responders)

    @classmethod
    def _responder_for(cls, name: str) -> Responder | None:
        with cls._lock:
            return cls._responders.get(name)

    # --- LLMProvider -------------------------------------------------------------------------
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
        """Structured generation with scripted or synthesised output (see module docstring)."""
        to_provider_schema(schema, "fake")  # same compatibility gate as the real providers
        operation = "vision" if images else "llm"
        input_chars = len(system) + len(prompt) + sum(len(f.data) // 50 for f in files)

        async def complete(followups: list[Turn]) -> Completion:
            attempt = CURRENT_ATTEMPT.get()
            self.calls.append(_call_record("json", model=model, schema=schema.__name__, prompt=prompt, system=system,
                                           attempt=attempt, images=len(images), files=len(files)))
            responder = self._responder_for(schema.__name__)
            if responder is not None:
                extras = {
                    "attempt": attempt, "problems": list(CURRENT_PROBLEMS.get()), "system": system, "model": model,
                    "images": list(images), "files": list(files),
                }
                text = _to_json_text(await _call_responder(responder, (prompt, schema), extras))
            else:
                options = synth.candidates(schema)
                text = json.dumps(options[min(attempt, len(options) - 1)], ensure_ascii=False)
            reprompt = sum(len(t.text) for t in followups)
            usage = Usage(
                provider="fake",
                model=model,
                operation=operation,
                input_tokens=_estimate_tokens("x" * (input_chars + reprompt)),
                output_tokens=_estimate_tokens(text),
                meta={"schema": schema.__name__, "attempt": attempt},
            )
            return Completion(text=text, usage=usage)

        return await run_structured(
            complete=complete,
            schema=schema,
            validate=validate,
            validation_retries=validation_retries,
            on_usage=on_usage,
            provider="fake",
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
        """Deterministic text: scripted via ``register_text`` or derived from the prompt."""
        self.calls.append(_call_record("text", model=model, prompt=prompt, system=system, attempt=0,
                                       images=len(images), files=len(files)))
        with self._lock:
            responder = self._text_responders.get("text")
        if responder is not None:
            text = str(await _call_responder(responder, (prompt, system), {
                "model": model, "images": list(images), "files": list(files)}))
        else:
            digest = hashlib.sha256(f"{system}\n{prompt}".encode()).hexdigest()[:8]
            words = " ".join(prompt.split()[:12])
            text = f"Fake response {digest}: {words}".strip()
        await emit_usage(
            on_usage,
            Usage(
                provider="fake",
                model=model,
                operation="vision" if images else "llm",
                input_tokens=_estimate_tokens(system + prompt),
                output_tokens=_estimate_tokens(text),
            ),
        )
        return text
