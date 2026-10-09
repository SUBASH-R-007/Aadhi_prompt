"""Manim subsystem contract.

    templates.registry: name -> Template                         (typed, tested, parameterised scenes)
    templates.validate_params(name, params) -> (model|None, problems)
    guard.check_code(code) -> list[str]                           (AST allow-list for free-form code)
    sandbox.run(...)                                              (docker | subprocess runner)
    render.render_manim(ctx, request) -> ManimRenderResult        (cached, sandboxed, self-healing, timed)
    qa.review_frames(ctx, video_path, request) -> list[str]       (vision-model layout critique)

Timing contract: the renderer receives ``beat_times`` (start of every beat relative to the scene
start) and ``total_duration``. Templates advance exactly one step per beat (``step_count(params)
== len(beats)``, linted as ``manim.beats_steps_mismatch``); free-form code calls
``self.wait_until_beat(i)`` before step ``i``. The produced video lasts exactly
``total_duration`` (last frame frozen) and stays in sync with the narration.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..schemas.screenplay import ManimSpec

TEMPLATE_API_VERSION = "1"


class ManimRenderRequest(BaseModel):
    spec: ManimSpec
    beat_times: list[float] = Field(default_factory=list)  # beat starts, relative to scene start
    beat_cues: list[str] = Field(default_factory=list)  # visual_cue per beat (repair/QA context)
    total_duration: float = 10.0
    target: Literal["fullscreen", "panel"] = "fullscreen"  # panel = narrow side panel framing
    title: str = ""
    narration_context: str = ""  # concatenated beat narration (repair prompts)
    background: str = "#1A0B2E"
    quality: Literal["l", "m", "h"] = "m"
    language: str = "en-IN"  # label language (fonts)
    # The lecture's AI engine for code repair and visual QA (None = the server default LLM_PROVIDER).
    llm_provider: Literal["gemini", "openai", "anthropic", "fake"] | None = None


class ManimRenderResult(BaseModel):
    asset_key: str
    storage_key: str
    duration: float
    width: int
    height: int
    cached: bool = False
    healed: bool = False  # free-form code was repaired by the LLM
    final_spec: ManimSpec  # spec actually rendered (repaired code / adjusted params)
    qa_issues: list[str] = Field(default_factory=list)
    log_tail: str = ""


class TemplateInfo(BaseModel):
    name: str
    title: str
    description: str  # when to use it (fed to planner/script prompts)
    params_schema: dict[str, Any]  # JSON schema of the params model (LLM-compatible)
    example_params: dict[str, Any]
    steps_hint: str = ""  # how params map to beats, e.g. "one step per item of `steps`"


@runtime_checkable
class Template(Protocol):
    name: str
    params_model: type[BaseModel]

    def step_count(self, params: BaseModel) -> int:
        """Number of animation steps == number of beats the scene must have."""
        ...

    def info(self) -> TemplateInfo: ...

    def render_source(self, params: BaseModel, *, target: str, language: str) -> str:
        """Python source of the ``construct`` body / scene class using AadhiScene helpers."""
        ...


# Why a render stopped (``ManimError.category``, ``SandboxResult.category``), in plain words for users.
FAILURE_MESSAGES: dict[str, str] = {
    "timeout": "the animation took longer than the time limit",
    "memory_limit": "the animation used more memory than allowed",
    "output_limit": "the animation wrote more data than allowed",
    "frame_limit": "the animation ran far past the length of the scene",
    "profile_limit": "the animation tried to change the resolution or frame rate",
    "blocked": "the animation tried an operation the sandbox does not allow (files, network or programs)",
    "unsafe_code": "the animation code failed the safety check",
    "latex": "a formula could not be typeset",
    "invalid_output": "the render did not produce a usable video",
    "repair_failed": "the automatic repair of the animation code failed",
    "invalid_request": "the animation request is invalid",
    "disabled": "Manim rendering is disabled for this request",
    "cancelled": "the render was cancelled",
    "render_failed": "the animation code raised an error",
}
FAILURE_CATEGORIES = tuple(FAILURE_MESSAGES)


class ManimError(Exception):
    """Render failed permanently (after repairs). ``log_tail`` has the last output lines.

    ``category`` (one of :data:`FAILURE_CATEGORIES`) says why; :attr:`friendly` is a short plain message.
    """

    def __init__(self, message: str, log_tail: str = "", category: str = "render_failed") -> None:
        super().__init__(message)
        self.log_tail = log_tail
        self.category = category if category in FAILURE_MESSAGES else "render_failed"

    @property
    def friendly(self) -> str:
        """Plain-words reason for users (no code, paths or log lines)."""
        return FAILURE_MESSAGES[self.category]
