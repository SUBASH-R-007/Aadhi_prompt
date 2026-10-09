"""The injected ``AadhiScene`` base: script assembly for the Manim sandbox.

Every scene script that reaches the sandbox is::

    # header: _AADHI_CFG = {...}            (beat times, duration, target, theme, fonts...)
    <aadhi/manim/runtime/scene_runtime.py>  (AadhiScene + safety patches; trusted)
    [<aadhi/manim/runtime/hardening.py>]    (frame budget, profile lock, audit hook; when requested)
    <scene source>                          (template output or AST-guarded free-form code)

The hardening block is appended only when ``assemble_script(..., hardening=build_hardening(...))``
is used (the renderer always does). It is not part of :func:`runtime_source`, so it never changes
render cache keys.

The runtime is kept in a real ``.py`` file so it is linted and readable; this module only reads it
as text. ``AADHI_SCENE_SOURCE`` is that text. Scene code subclasses ``AadhiScene`` and uses
``self.wait_until_beat(i)`` / ``self.play_step(i, ...)`` to stay in sync with the narration.
"""

from __future__ import annotations

import ast
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from .texcheck import MANIM_TEX_DENY_PATTERN

RUNTIME_PATH = Path(__file__).resolve().parent / "runtime" / "scene_runtime.py"
HARDENING_PATH = Path(__file__).resolve().parent / "runtime" / "hardening.py"
TEX_PROGRAMS = ("latex", "dvisvgm")  # the only programs the audit hook lets a render start
END_MARKER = "# ---- end of the Aadhi runtime; scene code follows ----"

THEME = {
    "gold": "#FFD700",
    "purple": "#B026FF",
    "cyan": "#00E5FF",
    "white": "#FFFFFF",
    "muted": "#B9A7D6",
    "green": "#3DDC97",
    "red": "#FF5C7A",
    "orange": "#FF9F43",
    "pink": "#FF6AD5",
    "blue": "#4DA3FF",
}
COLOR_NAMES = tuple(THEME)

# Font candidates per script, first installed one wins (Docker image: fonts-noto-core; Windows:
# Nirmala UI covers every Indic script). Missing fonts fall back to Pango's default + fallback.
SCRIPT_FONTS: dict[str, list[str]] = {
    "latin": ["Noto Sans", "Inter", "Segoe UI", "DejaVu Sans", "Liberation Sans", "Arial"],
    "tamil": ["Noto Sans Tamil", "Noto Sans Tamil UI", "Nirmala UI", "Latha", "Lohit Tamil"],
    "devanagari": ["Noto Sans Devanagari", "Noto Sans Devanagari UI", "Nirmala UI", "Mangal", "Lohit Devanagari"],
    "telugu": ["Noto Sans Telugu", "Noto Sans Telugu UI", "Nirmala UI", "Gautami", "Lohit Telugu"],
    "kannada": ["Noto Sans Kannada", "Noto Sans Kannada UI", "Nirmala UI", "Tunga", "Lohit Kannada"],
    "malayalam": ["Noto Sans Malayalam", "Noto Sans Malayalam UI", "Nirmala UI", "Kartika", "Lohit Malayalam"],
    "bengali": ["Noto Sans Bengali", "Noto Sans Bengali UI", "Nirmala UI", "Vrinda", "Lohit Bengali"],
    "gujarati": ["Noto Sans Gujarati", "Noto Sans Gujarati UI", "Nirmala UI", "Shruti", "Lohit Gujarati"],
    "gurmukhi": ["Noto Sans Gurmukhi", "Noto Sans Gurmukhi UI", "Nirmala UI", "Raavi", "Lohit Gurmukhi"],
    "oriya": ["Noto Sans Oriya", "Noto Sans Oriya UI", "Nirmala UI", "Kalinga", "Lohit Odia"],
}

# Output pixel sizes. Panels are portrait (4:5) so they read well in the narrow side panel.
FULLSCREEN_PIXELS = {"l": (854, 480), "m": (1280, 720), "h": (1920, 1080)}
PANEL_PIXELS = {"l": (480, 600), "m": (720, 900), "h": (1080, 1350)}
FRAME_RATES = {"l": 15, "m": 30, "h": 60}


@lru_cache(maxsize=1)
def runtime_source() -> str:
    """Text of the injected runtime (``AadhiScene`` and helpers)."""
    return RUNTIME_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def hardening_source() -> str:
    """Text of the sandbox hardening block (frame budget, profile lock, audit hook)."""
    return HARDENING_PATH.read_text(encoding="utf-8")


def build_hardening(*, total_duration: float, audit_hook: bool = True, has_latex: bool = True,
                    frame_limits: bool = True) -> dict[str, Any]:
    """The ``_AADHI_HARDEN`` dict consumed by the hardening block (JSON-safe values only)."""
    if not math.isfinite(float(total_duration)):
        raise ValueError("total_duration must be a finite number")
    return {
        "total_duration": round(float(total_duration), 3),
        "frame_limits": bool(frame_limits),
        "audit_hook": bool(audit_hook),
        "programs": list(TEX_PROGRAMS) if has_latex else [],
    }


def __getattr__(name: str) -> Any:  # lazy module attribute: AADHI_SCENE_SOURCE
    if name == "AADHI_SCENE_SOURCE":
        return runtime_source()
    raise AttributeError(name)


def frame_pixels(target: str, quality: str) -> tuple[int, int]:
    """(width, height) in pixels of the rendered video for ``target`` and ``quality``."""
    table = PANEL_PIXELS if target == "panel" else FULLSCREEN_PIXELS
    return table.get(quality, table["m"])


def build_config(
    *,
    beat_times: list[float],
    total_duration: float,
    target: str = "fullscreen",
    language: str = "en-IN",
    has_latex: bool = True,
    background: str = "#1A0B2E",
    quality: str = "m",
) -> dict[str, Any]:
    """The ``_AADHI_CFG`` dict consumed by the runtime (JSON-safe values only).

    Raises ``ValueError`` for non-finite times: ``nan``/``inf`` have no Python literal, so the
    generated header would fail before any scene code runs.
    """
    if not math.isfinite(float(total_duration)) or not all(math.isfinite(float(t)) for t in beat_times):
        raise ValueError("beat_times and total_duration must be finite numbers")
    width, height = frame_pixels(target, quality)
    return {
        "beat_times": [round(float(t), 3) for t in beat_times],
        "total_duration": round(float(total_duration), 3),
        "target": "panel" if target == "panel" else "fullscreen",
        "language": str(language),
        "has_latex": bool(has_latex),
        "background": str(background),
        "pixel_width": int(width),
        "pixel_height": int(height),
        "user_line_offset": 0,
        "tex_deny": MANIM_TEX_DENY_PATTERN,
        "script_fonts": {k: list(v) for k, v in SCRIPT_FONTS.items()},
    }


def _prefix(cfg: dict[str, Any], hardening: dict[str, Any] | None = None) -> str:
    header = (
        "# Generated by aadhi.manim: Aadhi runtime + scene code. Do not edit.\n"
        f"_AADHI_CFG = {cfg!r}\n"
    )
    body = runtime_source().rstrip("\n") + "\n\n"
    if hardening is not None:
        header += f"_AADHI_HARDEN = {dict(hardening)!r}\n"
        body += hardening_source().rstrip("\n") + "\n\n"
    return header + body + END_MARKER + "\n"


def assemble_script(scene_source: str, cfg: dict[str, Any], hardening: dict[str, Any] | None = None) -> str:
    """Full script = config header + runtime [+ hardening] + ``scene_source`` (user line numbers are tracked).

    ``hardening`` (see :func:`build_hardening`) appends the sandbox hardening block. It is meant for
    the sandbox process only (it installs a process-wide audit hook), never for in-process execution.
    """
    cfg = dict(cfg)
    cfg["user_line_offset"] = 0
    offset = _prefix(cfg, hardening).count("\n")
    cfg["user_line_offset"] = offset
    prefix = _prefix(cfg, hardening)
    assert prefix.count("\n") == offset  # the cfg and hardening reprs stay on one line
    return prefix + scene_source.rstrip("\n") + "\n"


def user_line_offset(script: str) -> int:
    """Number of lines before the scene code inside an assembled script."""
    head, sep, _tail = script.partition(END_MARKER + "\n")
    return (head + sep).count("\n") if sep else 0


def scene_class_name(source: str) -> str | None:
    """Name of the (first) class deriving from ``AadhiScene`` in ``source``; None when absent."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "AadhiScene":
                    return node.name
    return None
