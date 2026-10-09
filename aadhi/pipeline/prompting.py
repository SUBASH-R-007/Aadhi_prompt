"""Prompt files and prompt-assembly helpers.

Prompt texts live in ``aadhi/pipeline/prompts/*.md``; each starts with
``<!-- PROMPT_VERSION: n -->`` and the version of every file used is recorded in
``ProjectVersion.generation_meta['prompt_versions']``. Context is passed to the model as
Markdown sections with fenced JSON blocks (``json_section``) — models read them reliably and
the deterministic fake responders (``fake_content``) parse them back with ``extract_json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import SUPPORTED_LANGUAGES

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_VERSION_RE = re.compile(r"^\s*<!--\s*PROMPT_VERSION:\s*([A-Za-z0-9_.\-]+)\s*-->\s*\n?")


@dataclass(frozen=True)
class PromptFile:
    name: str
    version: str
    text: str  # body without the version header


@lru_cache(maxsize=64)
def load_prompt(name: str) -> PromptFile:
    """Load ``prompts/<name>.md`` (cached). Raises FileNotFoundError / ValueError (missing header)."""
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"invalid prompt name {name!r}")
    raw = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = _VERSION_RE.match(raw)
    if not m:
        raise ValueError(f"prompt {name}.md is missing its PROMPT_VERSION header")
    return PromptFile(name=name, version=m.group(1), text=raw[m.end():].strip())


def prompt_versions(names: list[str] | None = None) -> dict[str, str]:
    """``{name: version}`` for the given prompt files (default: every file in the directory)."""
    if names is None:
        names = sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
    return {n: load_prompt(n).version for n in names}


def system_prompt(*names: str) -> str:
    """Concatenate prompt bodies (e.g. ``system_prompt('style', 'plan')``)."""
    return "\n\n".join(load_prompt(n).text for n in names)


def language_label(code: str | None) -> str:
    """Human label for a language code, e.g. ``'Tamil (ta-IN)'``."""
    if not code:
        return ""
    return f"{SUPPORTED_LANGUAGES.get(code, code)} ({code})"


def section(title: str, body: str) -> str:
    """A Markdown section ``## title`` (empty body -> empty string)."""
    body = (body or "").strip()
    return f"## {title}\n{body}\n" if body else ""


def json_section(title: str, data: Any) -> str:
    """A Markdown section holding one fenced JSON block (parsed back by ``extract_json``)."""
    payload = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    return f"## {title}\n```json\n{payload}\n```\n"


def extract_json(prompt: str, title: str) -> Any | None:
    """Parse the JSON block of section ``title`` from a prompt built with ``json_section``."""
    marker = f"## {title}\n```json\n"
    start = prompt.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = prompt.find("\n```\n", start)
    if end < 0:
        return None
    try:
        return json.loads(prompt[start:end])
    except json.JSONDecodeError:
        return None


def join_sections(*parts: str) -> str:
    """Join non-empty prompt sections with blank lines."""
    return "\n".join(p for p in parts if p and p.strip())


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items if x)
