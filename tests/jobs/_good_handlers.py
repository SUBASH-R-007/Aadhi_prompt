"""A handler module that registers ``t_good`` when imported — for resolve_kinds tests."""

from __future__ import annotations

from typing import Any

from aadhi.jobs.base import job_handler


@job_handler("t_good")
async def good(ctx: Any) -> dict[str, Any]:
    """Trivial handler."""
    return {"ok": True}
