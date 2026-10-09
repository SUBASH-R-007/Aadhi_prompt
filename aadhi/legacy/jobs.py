"""``import_legacy`` job handler.

Payload (admin-only; enforced by the API):

* ``{"legacy_db_path": "<path to an existing v1 .db file>", "owner_fallback"?: "admin"}`` -- imports
  users + projects from a v1 ``projects.db`` (read-only);
* ``{"json_asset_key": "<asset key>"}`` -- imports one v1 (or v2) lecture JSON stored as an asset,
  owned by the job's user.

Result: ``{"project_ids": [...], ...summary}``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..jobs.base import FatalJobError, JobContext, job_handler
from ..schemas.screenplay import Screenplay
from ..security.strict_json import loads_strict
from .convert import convert_legacy, is_legacy
from .legacy_db import create_project_from_screenplay, import_legacy_db

__all__ = ["import_legacy_job", "parse_lecture_bytes", "screenplay_from_json"]

MAX_JSON_BYTES = 20 * 1024 * 1024


def screenplay_from_json(data: Any) -> tuple[Screenplay, list[str]]:
    """A v1 lecture (converted) or a v2 screenplay (validated) -> ``(Screenplay, warnings)``.

    Raises ``ValueError`` with a user-facing message when neither applies.
    """
    if is_legacy(data):
        return convert_legacy(data)
    if not (isinstance(data, dict) and isinstance(data.get("scenes"), list) and data["scenes"]):
        raise ValueError("not a v1 lecture or a v2 screenplay (no scenes found)")
    try:
        return Screenplay.model_validate(data), []
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise ValueError(f"not a v1 lecture or a valid v2 screenplay ({loc}: {first.get('msg', 'invalid')})") from exc


def parse_lecture_bytes(raw: bytes) -> tuple[Screenplay, list[str]]:
    """UTF-8 (BOM allowed) strict JSON -> ``screenplay_from_json``. Blocking: run it in a thread.

    Raises ``UnicodeDecodeError`` / ``ValueError`` (also for NaN/Infinity or out-of-range numbers).
    """
    return screenplay_from_json(loads_strict(raw.decode("utf-8-sig")))


def _db_path(payload: dict[str, Any]) -> Path:
    raw = payload.get("legacy_db_path")
    if not isinstance(raw, str) or not raw.strip():
        raise FatalJobError("legacy_db_path must be a path string", code="invalid_payload")
    path = Path(raw.strip())
    if path.suffix.lower() != ".db" or not path.is_file():
        raise FatalJobError("legacy_db_path must point to an existing .db file", code="invalid_payload")
    return path


async def _import_db(ctx: JobContext, path: Path) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    owner_fallback = str(ctx.payload.get("owner_fallback") or "admin")[:64]

    def on_progress(done: int, total: int) -> None:  # runs in the worker thread
        ctx.check_cancelled()
        fraction = 0.05 + 0.9 * (done / max(total, 1))
        loop.call_soon_threadsafe(ctx.progress, "import", fraction, f"Imported {done}/{total} v1 projects")

    def run() -> dict[str, Any]:
        with ctx.session() as db:
            return import_legacy_db(db, path, ctx.settings, owner_fallback=owner_fallback, on_progress=on_progress)

    ctx.progress("import", 0.02, "Reading the v1 database")
    try:
        result = await asyncio.to_thread(run)
    except (FileNotFoundError, ValueError) as exc:
        raise FatalJobError(ctx.settings.redact(str(exc)), code="invalid_payload") from exc
    for err in result["errors"]:
        ctx.log(f"Legacy import problem: {err}", level="warning")
    return result


async def _import_json(ctx: JobContext, key: str) -> dict[str, Any]:
    asset = await asyncio.to_thread(ctx.assets.get, key)
    if asset is None:
        raise FatalJobError("json_asset_key does not exist", code="invalid_payload")
    if asset.size_bytes > MAX_JSON_BYTES:
        raise FatalJobError("the JSON file is too large", code="invalid_payload")
    if ctx.user_id is None:
        raise FatalJobError("a JSON import needs an owning user", code="invalid_payload")
    raw = await asyncio.to_thread(ctx.storage.get_bytes, asset.storage_key)
    try:  # decoding + conversion is CPU-heavy for large lectures: keep it off the event loop
        screenplay, warnings = await asyncio.to_thread(parse_lecture_bytes, raw)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise FatalJobError(ctx.settings.redact(str(exc))[:500], code="invalid_payload") from exc

    def run() -> int:
        with ctx.session() as db:
            project, _ = create_project_from_screenplay(db, screenplay, owner_id=int(ctx.user_id), warnings=warnings)
            db.flush()
            return project.id

    project_id = await asyncio.to_thread(run)
    return {"project_ids": [project_id], "projects_imported": 1, "warning_count": len(warnings)}


@job_handler("import_legacy")
async def import_legacy_job(ctx: JobContext) -> dict[str, Any]:
    """Run a legacy import (see module docstring)."""
    payload = ctx.payload or {}
    if payload.get("legacy_db_path") is not None:
        path = await asyncio.to_thread(_db_path, payload)  # stats the filesystem
        result = await _import_db(ctx, path)
    elif isinstance(payload.get("json_asset_key"), str) and payload["json_asset_key"]:
        result = await _import_json(ctx, payload["json_asset_key"])
    else:
        raise FatalJobError("payload needs legacy_db_path or json_asset_key", code="invalid_payload")
    ctx.progress("import", 1.0, f"Imported {result.get('projects_imported', 0)} project(s)")
    return result
