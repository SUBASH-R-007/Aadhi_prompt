"""Versioned prompt files for the Manim subsystem.

Each ``*.md`` file starts with ``<!-- PROMPT_VERSION: name-N -->``. ``{{freeform_rules}}`` inside a
prompt is replaced with ``freeform_rules.md`` so the generation rules and the repair rules never
diverge. The script stage can embed :func:`freeform_rules` in its simulation-scene prompt.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
_VERSION = re.compile(r"^\s*<!--\s*PROMPT_VERSION:\s*([A-Za-z0-9_.\-]+)\s*-->\s*\n?")


@lru_cache(maxsize=16)
def _raw(name: str) -> str:
    if not re.fullmatch(r"[a-z_]+\.md", name):
        raise ValueError(f"invalid prompt name {name!r}")
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """The ``PROMPT_VERSION`` declared by prompt ``name`` ('' when absent)."""
    match = _VERSION.match(_raw(name))
    return match.group(1) if match else ""


def load_prompt(name: str) -> str:
    """Prompt text without its version header, with ``{{freeform_rules}}`` expanded."""
    text = _VERSION.sub("", _raw(name), count=1).strip()
    if "{{freeform_rules}}" in text:
        text = text.replace("{{freeform_rules}}", freeform_rules())
    return text


def freeform_rules() -> str:
    """Rules for writing free-form AadhiScene code (shared by generation and repair prompts)."""
    return _VERSION.sub("", _raw("freeform_rules.md"), count=1).strip()
