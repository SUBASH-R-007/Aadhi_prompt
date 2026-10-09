"""TeX safety checks for strings that Manim compiles with a *real* LaTeX engine.

The browser renders TeX with MathJax (``aadhi.schemas.screenplay._check_latex`` guards the macros
MathJax would turn into links/attributes). Manim instead runs ``latex`` on the host/sandbox, so the
file-access and catcode tricks of TeX itself must be refused as well: ``\\input`` and friends read
arbitrary files into the video, ``^^5c`` spells a backslash, ``\\csname``/``\\expandafter`` assemble
forbidden control sequences, ``\\write``/``filecontents`` write files.

The same pattern is enforced *inside* the sandbox (the injected runtime checks every expression
before it reaches LaTeX), so strings assembled at runtime by free-form code cannot bypass it.
"""

from __future__ import annotations

import re

from ..schemas.screenplay import _check_latex

MAX_TEX_LENGTH = 1200

# Kept as a plain string: it is also embedded verbatim into the sandbox runtime.
MANIM_TEX_DENY_PATTERN = (
    r"\^\^"
    r"|filecontents"
    r"|\\(?:[A-Za-z@]*input[A-Za-z@]*|include[A-Za-z]*|openin|openout|closein|closeout|read|readline"
    r"|write|immediate|catcode|lccode|uccode|sfcode|mathcode|delcode|csname|endcsname|expandafter"
    r"|noexpand|scantokens|lowercase|uppercase|usepackage|RequirePackage|documentclass|directlua"
    r"|latelua|special|pdf[A-Za-z]*|shipout|output|everyjob|everypar|everymath|everydisplay|everyhbox"
    r"|everyvbox|everycr|font|makeatletter|makeatother|def|edef|gdef|xdef|let|futurelet|newcommand"
    r"|renewcommand|providecommand|DeclareRobustCommand|newenvironment|renewenvironment|href|url"
    r"|batchmode|nonstopmode|scrollmode|errorstopmode|tracing[A-Za-z]*|endinput|globaldefs|ifeof)"
    r"(?![A-Za-z@])"
)
_DENY = re.compile(MANIM_TEX_DENY_PATTERN)


def tex_problem(tex: str) -> str | None:
    """Return a human-readable problem with ``tex`` or ``None`` when it is safe to compile."""
    if not isinstance(tex, str):
        return "latex must be a string"
    if len(tex) > MAX_TEX_LENGTH:
        return f"latex is too long ({len(tex)} > {MAX_TEX_LENGTH} characters)"
    try:
        _check_latex(tex)
    except ValueError as exc:
        return str(exc)
    match = _DENY.search(tex)
    if match:
        return f"latex uses a forbidden construct {match.group(0)!r}"
    depth = 0
    escaped = False
    for ch in tex:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return "latex has an unmatched '}'"
    if depth:
        return "latex has an unmatched '{'"
    return None


def check_tex(value: str) -> str:
    """Pydantic-style validator: raise ``ValueError`` for unsafe/broken TeX, else return it."""
    problem = tex_problem(value)
    if problem:
        raise ValueError(problem)
    return value
