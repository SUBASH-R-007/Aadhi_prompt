"""Provider-independent structured-generation loop: parse -> validate -> semantic check -> re-ask.

Every LLM adapter (real or fake) supplies a ``complete(followups)`` coroutine that sends the
original request plus ``followups`` (alternating model/user turns) and returns the raw text with
its usage. ``run_structured`` drives the loop described in ``aadhi.providers.base.LLMProvider``:

1. parse the text as JSON (code fences and stray prose are tolerated);
2. ``schema.model_validate``;
3. the optional semantic ``validate`` hook;
4. on any problem, re-ask with the previous output and the problem list (at most
   ``validation_retries`` times), then raise ``ProviderError``.

Safety blocks raise ``ContentBlocked`` immediately. A *recitation* stop (the answer reproduced
source text verbatim, common when lectures are generated from an uploaded textbook) is a
problem like any other and is re-asked with a paraphrase instruction; only when the last attempt
still recites is ``ContentBlocked`` raised.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from ...config import Settings
from .._common import emit_usage, make_error
from ..base import ContentBlocked, ProviderError, SemanticValidator, Usage, UsageSink

T = TypeVar("T", bound=BaseModel)

#: Zero-based attempt number of the structured call currently being made (0 = first ask).
CURRENT_ATTEMPT: ContextVar[int] = ContextVar("aadhi_llm_attempt", default=0)
#: Problems reported for the previous attempt (empty on the first ask).
CURRENT_PROBLEMS: ContextVar[tuple[str, ...]] = ContextVar("aadhi_llm_problems", default=())

MAX_PROBLEMS = 25
MAX_ECHO_CHARS = 100_000
RECITATION_PROBLEM = (
    "the answer was stopped because it reproduced source text verbatim; rewrite it in your own words "
    "(paraphrase and summarise instead of copying sentences from the source)"
)
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


@dataclass
class Turn:
    """A follow-up conversation turn after the original request."""

    role: Literal["user", "model"]
    text: str


@dataclass
class Completion:
    """Raw provider answer for one request."""

    text: str
    usage: Usage | None = None  # the attempt that produced ``text`` (or the final refusal)
    truncated: bool = False  # stopped at max_output_tokens
    blocked: str | None = None  # safety block / refusal detail (usage is still reported)
    recitation: bool = False  # stopped for reciting source text: re-ask with a paraphrase instruction
    # Earlier billed attempts inside the same request, oldest first (e.g. a Claude model that declined
    # before a server-side refusal fallback answered). Reported before ``usage``.
    extra_usage: list[Usage] = field(default_factory=list)


CompleteFn = Callable[[list[Turn]], Awaitable[Completion]]


def strip_code_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence (```json ... ```), if any."""
    s = (text or "").strip()
    m = _FENCE.match(s)
    return m.group(1).strip() if m else s


def parse_json_text(text: str) -> Any:
    """Parse model output as JSON, tolerating code fences and prose around one JSON object.

    Raises ``ValueError`` with a short, model-facing explanation.
    """
    s = strip_code_fences(text)
    if not s:
        raise ValueError("the response was empty")
    try:
        return json.loads(s)
    except json.JSONDecodeError as first:
        start, end = s.find("{"), s.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"the response is not valid JSON ({first.msg} at line {first.lineno} column {first.colno})") from None


def _loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "<root>"


def format_validation_error(exc: ValidationError, limit: int = MAX_PROBLEMS) -> list[str]:
    """Human/model-readable ``path: message`` lines for a pydantic ``ValidationError``."""
    lines: list[str] = []
    for err in exc.errors(include_url=False)[:limit]:
        lines.append(f"{_loc(tuple(err.get('loc') or ()))}: {err.get('msg', 'invalid value')}")
    extra = exc.error_count() - len(lines)
    if extra > 0:
        lines.append(f"... and {extra} more validation errors")
    return lines


def reask_message(problems: list[str]) -> str:
    """Follow-up user turn asking the model to fix its previous JSON."""
    bullet = "\n".join(f"- {p}" for p in problems[:MAX_PROBLEMS])
    return (
        "Your previous response (above) cannot be accepted. Problems found:\n"
        f"{bullet}\n\n"
        "Return the COMPLETE corrected JSON object that satisfies the response schema - not a diff and "
        "no commentary. Keep everything that was already correct."
    )


async def settle(completion: Completion, *, on_usage: UsageSink | None, provider: str, what: str,
                 settings: Settings | None) -> None:
    """Report the completion's usage (earlier attempts first), then raise ``ContentBlocked`` if it was blocked."""
    for usage in (*completion.extra_usage, completion.usage):
        if usage is not None:
            await emit_usage(on_usage, usage)
    if completion.blocked:
        raise make_error(ContentBlocked, provider, f"{what} blocked by safety filters", settings=settings,
                         detail=completion.blocked)


def recitation_error(provider: str, what: str, settings: Settings | None) -> ContentBlocked:
    """``ContentBlocked`` raised when the final attempt still stopped for reciting source text."""
    return make_error(ContentBlocked, provider, f"{what} kept reproducing source text verbatim",
                      settings=settings, detail="finish reason recitation")


def recitation_followups(completion: Completion) -> list[Turn]:
    """Follow-up turns asking the model to paraphrase after a recitation stop."""
    return [Turn("model", completion.text[:MAX_ECHO_CHARS] or "(empty response)"),
            Turn("user", f"Your previous answer cannot be used: {RECITATION_PROBLEM}.")]


def check_output(text: str, schema: type[T], validate: SemanticValidator | None, truncated: bool) -> tuple[T | None, list[str]]:
    """Parse + validate ``text``; return ``(obj, [])`` or ``(None, problems)``."""
    try:
        data = parse_json_text(text)
    except ValueError as exc:
        problems = [str(exc)]
        if truncated:
            problems.append("the output was cut off at the length limit: answer more concisely")
        return None, problems
    if not isinstance(data, dict):
        return None, [f"the top-level JSON value must be an object, got {type(data).__name__}"]
    try:
        obj = schema.model_validate(data)
    except ValidationError as exc:
        return None, format_validation_error(exc)
    if validate is not None:
        problems = [str(p) for p in (validate(obj) or []) if str(p).strip()]
        if problems:
            return None, problems[:MAX_PROBLEMS]
    return obj, []


async def run_structured(
    *,
    complete: CompleteFn,
    schema: type[T],
    validate: SemanticValidator | None,
    validation_retries: int,
    on_usage: UsageSink | None,
    provider: str,
    settings: Settings | None,
) -> T:
    """Drive the parse/validate/re-ask loop around ``complete`` (see module docstring)."""
    followups: list[Turn] = []
    problems: list[str] = []
    attempts = max(0, int(validation_retries)) + 1
    for attempt in range(attempts):
        tok_a = CURRENT_ATTEMPT.set(attempt)
        tok_p = CURRENT_PROBLEMS.set(tuple(problems))
        try:
            completion = await complete(followups)
        finally:
            CURRENT_ATTEMPT.reset(tok_a)
            CURRENT_PROBLEMS.reset(tok_p)
        await settle(completion, on_usage=on_usage, provider=provider, what=f"{schema.__name__} generation",
                     settings=settings)
        if completion.recitation:
            if attempt == attempts - 1:
                raise recitation_error(provider, f"{schema.__name__} generation", settings)
            problems = [RECITATION_PROBLEM]
        else:
            obj, problems = check_output(completion.text, schema, validate, completion.truncated)
            if obj is not None:
                return obj
        followups = [
            Turn("model", completion.text[:MAX_ECHO_CHARS] or "(empty response)"),
            Turn("user", reask_message(problems)),
        ]
    raise make_error(
        ProviderError,
        provider,
        f"{schema.__name__} output still invalid after {attempts} attempt(s)",
        settings=settings,
        detail="; ".join(problems[:5]),
    )
