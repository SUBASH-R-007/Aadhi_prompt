"""Guarded access to sibling subsystems (providers, job limits, manim, compose, usage).

The pipeline is written in parallel with those areas, so every cross-area call goes through this
module: imports are lazy, absent modules degrade gracefully (documented per function) and tests
can monkeypatch a single seam (``aadhi.pipeline.integrations.get_llm`` …) instead of reaching into
other packages.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..providers.base import ProviderNotConfigured, Usage

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings
    from ..manim.base import ManimRenderRequest, ManimRenderResult, Template, TemplateInfo
    from ..providers.base import GifProvider, ImageProvider, LLMProvider, TTSProvider, VideoProvider
    from ..schemas.screenplay import LexiconEntry, ManimSpec
    from .base import GenerationOptions

log = logging.getLogger(__name__)

LimitName = str  # "llm" | "tts" | "manim" | "render" | "image" | "video"


# --- providers ------------------------------------------------------------------


def _factory() -> Any:
    try:
        from ..providers import factory
    except ImportError as exc:  # pragma: no cover - partial installs during development
        raise ProviderNotConfigured("provider factory is not installed", provider="factory") from exc
    return factory


def llm_engine(options: GenerationOptions | None, settings: Settings) -> str:
    """The lecture's AI engine: ``options.llm_provider``, else the server default ``LLM_PROVIDER``."""
    chosen = options.llm_provider if options is not None else None
    return chosen or settings.llm_provider


def get_llm(settings: Settings, engine: str | None = None) -> LLMProvider:
    """LLM provider for ``engine`` (None = ``LLM_PROVIDER``; raises ProviderNotConfigured when unavailable).

    With the offline ``fake`` provider the realistic pipeline responders (``fake_content``) are
    registered for every response model that has no scripted responder yet, so a dev demo with
    ``LLM_PROVIDER=fake`` yields a coherent lecture. Responders registered by tests are kept.
    """
    llm = _factory().get_llm(settings, engine=engine)
    if getattr(llm, "name", "") == "fake":
        from .fake_content import ensure_fake_responders

        ensure_fake_responders()
    return llm


def llm_model(settings: Settings, tier: str, options: GenerationOptions | None = None, *,
              override: str | None = None) -> str:
    """Model of ``tier`` (plan | script | critic | fast) for the lecture's engine (``llm_engine``).

    ``override`` (an admin's allow-listed model) wins only when it belongs to that engine (any model
    with the offline fake engine); otherwise the engine's configured model for the tier is used.
    """
    factory = _factory()
    engine = llm_engine(options, settings)
    if override and (engine == "fake" or factory.engine_for_model(override) == engine):
        return override
    return factory.llm_models(settings, engine)[tier]


def get_tts(name: str | None, settings: Settings) -> TTSProvider:
    """TTS provider ``name`` (None = settings default)."""
    return _factory().get_tts(name, settings)


def get_image(settings: Settings) -> ImageProvider:
    """Image generation provider."""
    return _factory().get_image(settings)


def get_video(settings: Settings) -> VideoProvider:
    """Video generation provider."""
    return _factory().get_video(settings)


def get_gif(settings: Settings) -> GifProvider:
    """GIF search provider."""
    return _factory().get_gif(settings)


# --- process-wide limits ------------------------------------------------------------


@asynccontextmanager
async def limit(name: LimitName) -> AsyncIterator[None]:
    """Acquire the process-wide semaphore ``name`` from ``aadhi.jobs.limits`` (no-op if absent)."""
    try:
        from ..jobs.limits import acquire
    except ImportError:
        yield
        return
    async with acquire(name):  # type: ignore[arg-type]
        yield


# --- speech text -----------------------------------------------------------------------

_MARKUP_RE = re.compile(r"\*\*|(?<!\\)\*|`|\[\[|\]\]")


def apply_lexicon(text: str, lexicon: list[LexiconEntry], language: str) -> str:
    """Apply pronunciation rules (providers implementation; whole-word fallback)."""
    try:
        from ..providers.tts.normalize import apply_lexicon as impl
    except ImportError:
        impl = None
    if impl is not None:
        return impl(text, lexicon, language)
    out = text
    for entry in sorted(lexicon, key=lambda e: -len(e.written)):
        if entry.language not in (None, language):
            continue
        pattern = re.compile(rf"(?<![\w]){re.escape(entry.written)}(?![\w])")
        out = pattern.sub(entry.spoken, out)
    return out


def normalize_for_speech(text: str, language: str) -> str:
    """Normalise text for TTS (providers implementation; markup-stripping fallback)."""
    try:
        from ..providers.tts.normalize import normalize_for_speech as impl
    except ImportError:
        impl = None
    if impl is not None:
        return impl(text, language)
    cleaned = _MARKUP_RE.sub("", text).replace("$", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def speech_text(text: str, lexicon: list[LexiconEntry], language: str) -> str:
    """Text actually sent to TTS: ``normalize_for_speech(apply_lexicon(text))``."""
    return normalize_for_speech(apply_lexicon(text, lexicon, language), language)


def default_voice(provider: str, language: str, tts: TTSProvider | None = None) -> str:
    """Default voice for ``provider``/``language`` (voices table, else the provider's own default)."""
    try:
        from ..providers.tts.voices import default_voice as impl
    except ImportError:
        impl = None
    if impl is not None:
        try:
            voice = impl(provider, language)
            if voice:
                return voice
        except (KeyError, ValueError):
            pass
    if tts is not None:
        return tts.default_voice(language)
    return ""


# --- manim -------------------------------------------------------------------------------


def _templates_module() -> Any | None:
    try:
        from ..manim import templates
    except ImportError:
        return None
    return templates


def list_templates() -> list[TemplateInfo]:
    """Manim template catalogue ([] when the manim area is not installed)."""
    mod = _templates_module()
    if mod is None:
        return []
    try:
        return list(mod.list_templates())
    except Exception:  # pragma: no cover - defensive: a broken template must not stop planning
        log.exception("listing manim templates failed")
        return []


def get_template(name: str | None) -> Template | None:
    """Template by name, or None (unknown name / manim area missing)."""
    if not name:
        return None
    mod = _templates_module()
    if mod is None:
        return None
    try:
        return mod.get_template(name)
    except KeyError:
        return None


def templates_available() -> bool:
    """True when the manim template registry can be consulted."""
    return _templates_module() is not None


def validate_template_params(name: str, params: dict[str, Any]) -> tuple[BaseModel | None, list[str]]:
    """``(params_model_instance | None, problems)``; unknown template -> problem."""
    mod = _templates_module()
    if mod is None:
        return None, []
    impl = getattr(mod, "validate_params", None)
    if impl is not None:
        return impl(name, params)
    tpl = get_template(name)
    if tpl is None:
        return None, [f"unknown manim template {name!r}"]
    try:
        return tpl.params_model.model_validate(params), []
    except Exception as exc:  # pydantic.ValidationError
        return None, [str(exc)]


def template_step_count(name: str, params: dict[str, Any]) -> int | None:
    """Number of animation steps for template params (None when unknown/invalid)."""
    tpl = get_template(name)
    if tpl is None:
        return None
    model, problems = validate_template_params(name, params)
    if model is None or problems:
        return None
    try:
        return int(tpl.step_count(model))
    except Exception:  # pragma: no cover - defensive
        return None


_FORBIDDEN_CALLS = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "input", "breakpoint",
    "getattr", "setattr", "delattr",
}
_ALLOWED_IMPORTS = {"manim", "math", "numpy", "random", "itertools", "functools"}


def _fallback_code_check(code: str) -> list[str]:
    """Minimal AST allow-list used only when ``aadhi.manim.guard`` is not installed."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                if n.split(".")[0] not in _ALLOWED_IMPORTS:
                    problems.append(f"import of {n!r} is not allowed")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_CALLS:
            problems.append(f"use of {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"dunder attribute {node.attr!r} is not allowed")
    return problems


def check_manim_code(code: str) -> list[str]:
    """AST allow-list problems for free-form Manim code (guard module, else a minimal fallback)."""
    try:
        from ..manim.guard import check_code
    except ImportError:
        return _fallback_code_check(code)
    return list(check_code(code))


def manim_spec_problems(spec: ManimSpec, n_beats: int | None) -> list[str] | None:
    """``aadhi.manim.validate_spec`` problems, or None when that function is unavailable."""
    try:
        from .. import manim as manim_pkg
    except ImportError:  # pragma: no cover
        return None
    impl = getattr(manim_pkg, "validate_spec", None)
    if impl is None:
        return None
    return list(impl(spec, n_beats))


RenderManim = Callable[[Any, "ManimRenderRequest"], Awaitable["ManimRenderResult"]]


def render_manim_fn() -> RenderManim | None:
    """``aadhi.manim.render.render_manim`` or None when the manim renderer is not installed."""
    try:
        from ..manim.render import render_manim
    except ImportError:
        return None
    return render_manim


# --- compose -------------------------------------------------------------------------------


def build_timeline_fn() -> Callable[..., Any] | None:
    """``aadhi.compose.timeline.build_timeline`` or None when compose is not installed."""
    try:
        from ..compose.timeline import build_timeline
    except ImportError:
        return None
    return build_timeline


# --- usage --------------------------------------------------------------------------------


def estimate_cost(usage: Usage, settings: Settings, default: float = 0.0) -> float:
    """Price a prospective usage (``aadhi.usage.pricing``), ``default`` when pricing is unavailable."""
    try:
        from ..usage.pricing import estimate_cost as impl
    except ImportError:
        return default
    try:
        return float(impl(usage, settings))
    except Exception:  # pragma: no cover - defensive
        return default
