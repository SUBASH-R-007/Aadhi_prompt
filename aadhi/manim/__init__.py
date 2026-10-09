"""Manim subsystem: template library, AadhiScene runtime, AST guard, sandboxes, cached render, QA.

Public entry points (see ``docs/INTERFACES.md``)::

    aadhi.manim.validate_spec(spec, n_beats)                  -> list[str]
    aadhi.manim.templates.registry / list_templates / get_template / validate_params
    aadhi.manim.guard.check_code(code)                        -> list[str]
    aadhi.manim.render.render_manim(ctx, request)             -> ManimRenderResult (raises ManimError)
    aadhi.manim.qa.review_frames(ctx, video_path, request)    -> list[str]

Importing this package is cheap: Manim itself is only ever imported inside the sandbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..schemas.screenplay import ManimSpec

__all__ = ["validate_spec"]


def validate_spec(spec: ManimSpec, n_beats: int | None = None) -> list[str]:
    """Problems with a ``ManimSpec`` (template params, step/beat count, free-form code guard).

    Messages are prefixed with a lint-friendly category (``manim.template_unknown``,
    ``manim.params_invalid``, ``manim.beats_steps_mismatch``, ``manim.code_forbidden``) so the
    pipeline can map them to issue codes; ``[]`` means the spec can be rendered.
    """
    from . import guard
    from .templates import registry, validate_params

    problems: list[str] = []
    if spec.template:
        if spec.template not in registry:
            return [f"manim.template_unknown: unknown template {spec.template!r} (available: {', '.join(sorted(registry))})"]
        model, errors = validate_params(spec.template, spec.params)
        if errors or model is None:
            return [f"manim.params_invalid: {e}" for e in errors]
        steps = registry[spec.template].step_count(model)
        if n_beats is not None and n_beats != steps:
            problems.append(
                f"manim.beats_steps_mismatch: template {spec.template!r} has {steps} animation step(s) but the scene "
                f"has {n_beats} beat(s); make them equal (one step per beat)"
            )
        return problems
    code = spec.code or ""
    problems.extend(f"manim.code_forbidden: {p}" for p in guard.check_code(code))
    if not problems:
        problems.extend(f"manim.beats_steps_mismatch: {p}" for p in guard.check_timing(code, n_beats))
    return problems
