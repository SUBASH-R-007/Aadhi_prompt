"""LLM access and response schemas for Manim repair and visual QA.

The provider is obtained lazily through ``aadhi.providers.factory.get_llm`` (tests monkeypatch
:func:`get_llm`) for the lecture's AI engine (``ManimRenderRequest.llm_provider``; None = the server
default), and both calls use that engine's fast model (:func:`fast_model`). Schemas are
LLM-compatible: flat objects, lists of small objects, enums.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings
    from ..providers.base import LLMProvider

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*\n(.*?)\n?```\s*$", re.S)


class ManimCodeFix(BaseModel):
    """Repaired free-form scene code."""

    diagnosis: str = Field(default="", max_length=800, description="one or two sentences: what was wrong")
    code: str = Field(min_length=1, max_length=20000, description="the complete corrected Python scene code")


class FrameIssue(BaseModel):
    frame: int = Field(ge=1, le=4, description="frame number (1-4)")
    kind: Literal["overlap", "cut_off", "unreadable", "empty", "other"]
    description: str = Field(max_length=300, description="what is wrong and where")


class FrameReview(BaseModel):
    """Visual QA verdict for the sampled frames."""

    issues: list[FrameIssue] = Field(default_factory=list, max_length=12)


def get_llm(settings: Settings, engine: str | None = None) -> LLMProvider:
    """The LLM provider of ``engine`` (None = ``LLM_PROVIDER``; fake in tests)."""
    from ..providers.factory import get_llm as factory_get_llm

    return factory_get_llm(settings, engine=engine)


def fast_model(settings: Settings, engine: str | None = None) -> str:
    """The fast-tier model of ``engine`` (None = ``LLM_PROVIDER``): code repair and visual QA."""
    from ..providers.factory import llm_models

    return llm_models(settings, engine)["fast"]


def strip_code_fences(code: str) -> str:
    """Remove a surrounding Markdown code fence the model may have added."""
    match = _FENCE.match(code)
    return (match.group(1) if match else code).strip("\n") + "\n"
