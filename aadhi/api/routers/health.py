"""Liveness (``/healthz``) and readiness (``/readyz``: database + storage)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...db import ping
from ..deps import DbSession, Store

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

PROBE_KEY = "assets/readyz/probe.txt"


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """The process is up."""
    return {"ok": True}


@router.get("/readyz")
def readyz(db: DbSession, store: Store) -> JSONResponse:
    """Database answers and storage is reachable (503 otherwise)."""
    checks = {"db": False, "storage": False}
    try:
        checks["db"] = bool(ping(db))
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it never raises
        logger.warning("readiness: database check failed: %s", type(exc).__name__)
    try:
        storage = store.storage
        root = getattr(storage, "root", None)
        if root is not None:
            checks["storage"] = bool(root.is_dir())
        else:
            storage.exists(PROBE_KEY)  # remote backends: a HEAD that must not raise
            checks["storage"] = True
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it never raises
        logger.warning("readiness: storage check failed: %s", type(exc).__name__)
    ok = all(checks.values())
    body: dict[str, Any] = {"ok": ok, **checks}
    if not ok:
        body.update({"code": "unavailable", "detail": "Not ready."})
    return JSONResponse(body, status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})
