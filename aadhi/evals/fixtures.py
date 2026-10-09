"""Eval fixture discovery.

A fixture directory holds:

* source documents (``.pdf``, ``.docx``, ``.txt``, ``.md``) that go through the full pipeline;
* screenplay documents (``.json``: a v2 ``Screenplay`` or a v1 legacy lecture) that skip generation
  and are only linted/measured (baselines, regression samples);
* optional sidecars ``<stem>.options.json`` with ``GenerationOptions`` overrides for that fixture.

Files starting with ``.`` or ``_`` and ``README*`` are ignored.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..pipeline.base import GenerationOptions

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SOURCE_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": DOCX_MIME,
    ".txt": "text/plain",
    ".md": "text/markdown",
}
SCREENPLAY_SUFFIX = ".json"
OPTIONS_SUFFIX = ".options.json"
# Fixture names become directories under --out, which the runner empties before each fixture;
# "workspace" is where --keep-workspace stores the eval database.
RESERVED_NAMES = frozenset({"workspace"})


class FixtureError(ValueError):
    """Invalid fixture directory or sidecar."""


@dataclass(frozen=True)
class Fixture:
    """One eval input."""

    name: str
    path: Path
    kind: Literal["source", "screenplay"]
    mime: str
    options: Mapping[str, Any] = field(default_factory=dict)  # sidecar overrides


def _is_ignored(path: Path) -> bool:
    return path.name.startswith((".", "_")) or path.name.lower().startswith("readme") or not path.is_file()


def _load_sidecar(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read options sidecar {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise FixtureError(f"options sidecar {path.name} must contain a JSON object")
    return data


def discover_fixtures(directory: Path, only: Iterable[str] = ()) -> list[Fixture]:
    """List fixtures in ``directory`` (sorted by name), optionally filtered to names in ``only``."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FixtureError(f"fixture directory {directory} does not exist")
    wanted = {o.strip() for o in only if o.strip()}
    fixtures: dict[str, Fixture] = {}
    for path in sorted(directory.iterdir()):
        if _is_ignored(path) or path.name.endswith(OPTIONS_SUFFIX):
            continue
        suffix = path.suffix.lower()
        if suffix in SOURCE_TYPES:
            kind, mime = "source", SOURCE_TYPES[suffix]
        elif suffix == SCREENPLAY_SUFFIX:
            kind, mime = "screenplay", "application/json"
        else:
            continue
        name = path.stem
        if name.casefold() in RESERVED_NAMES:
            raise FixtureError(f"fixture name {name!r} is reserved (rename {path.name})")
        if name in fixtures:
            raise FixtureError(f"two fixtures share the name {name!r}: {fixtures[name].path.name} and {path.name}")
        sidecar = directory / f"{name}{OPTIONS_SUFFIX}"
        options = _load_sidecar(sidecar) if sidecar.is_file() else {}
        fixtures[name] = Fixture(name=name, path=path, kind=kind, mime=mime, options=options)
    if wanted:
        missing = sorted(wanted - fixtures.keys())
        if missing:
            raise FixtureError(f"unknown fixtures: {', '.join(missing)} (have: {', '.join(sorted(fixtures))})")
        return [fixtures[n] for n in sorted(fixtures) if n in wanted]
    return [fixtures[n] for n in sorted(fixtures)]


def resolve_options(fixture: Fixture, overrides: Mapping[str, Any] | None = None) -> GenerationOptions:
    """Defaults <- fixture sidecar <- run-wide overrides, validated as ``GenerationOptions``."""
    merged: dict[str, Any] = {**dict(fixture.options), **dict(overrides or {})}
    unknown = sorted(set(merged) - set(GenerationOptions.model_fields))
    if unknown:  # GenerationOptions ignores unknown keys; a typo would silently change an eval
        raise FixtureError(f"unknown option(s) for fixture {fixture.name}: {', '.join(unknown)}")
    try:
        return GenerationOptions.model_validate(merged)
    except ValueError as exc:
        raise FixtureError(f"invalid options for fixture {fixture.name}: {exc}") from exc
