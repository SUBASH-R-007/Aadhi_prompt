"""Generate ``web/schemas/*.schema.json`` from the pydantic models (the frontend's copy of the contract).

    python -m aadhi.schemas.generate            # rewrite web/schemas/screenplay.schema.json and timeline.schema.json
    python -m aadhi.schemas.generate --check    # exit 1 if a file is out of date (CI; tests/schemas/test_v3_schema.py)

The files are ``json.dumps(schema, indent=1)`` without a trailing newline, as they were first written. The JSDoc
mirrors (``web/js/shared/types.js``) are kept by hand next to them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .screenplay import screenplay_json_schema
from .timeline import timeline_json_schema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "web" / "schemas"
GENERATED: dict[str, Callable[[], dict[str, Any]]] = {
    "screenplay.schema.json": screenplay_json_schema,
    "timeline.schema.json": timeline_json_schema,
}


def render(name: str) -> str:
    """The text ``web/schemas/<name>`` must hold."""
    return json.dumps(GENERATED[name](), indent=1)


def stale_files(directory: Path = SCHEMA_DIR) -> list[str]:
    """Names of the generated files that are missing or differ from the models."""
    out = []
    for name in GENERATED:
        path = directory / name
        try:
            current = path.read_bytes().decode("utf-8")
        except OSError:
            current = None
        if current != render(name):
            out.append(name)
    return out


def write_all(directory: Path = SCHEMA_DIR) -> list[str]:
    """Rewrite the stale files; returns their names."""
    changed = stale_files(directory)
    for name in changed:
        (directory / name).write_bytes(render(name).encode("utf-8"))
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a generated file is out of date")
    args = parser.parse_args(argv)
    if args.check:
        stale = stale_files()
        for name in stale:
            print(f"web/schemas/{name} is out of date: run python -m aadhi.schemas.generate", file=sys.stderr)
        return 1 if stale else 0
    for name in write_all():
        print(f"wrote web/schemas/{name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
