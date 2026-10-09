"""Learner analytics: event ingest (public via share token, or session) and the dashboard.

Ingest throttling (all per share link, or per version for session ingest; nothing a single client
does can lock out the rest of a class):

* requests per minute per ``ip|viewer_id`` (the general API limit) and per client IP (a multiple
  of it, so a whole class behind one campus NAT fits);
* events per UTC day per client IP (``IP_DAILY_EVENT_CAP``) and per viewer id
  (``VIEWER_DAILY_EVENT_CAP``), both well below the share-wide cap: only the offending IP/viewer
  gets 429;
* events per UTC day per share link (``SHARE_DAILY_EVENT_CAP``): the hard storage bound, counted in
  the database (reaching it takes several distinct client IPs).
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import ANALYTICS_EVENTS, AnalyticsEvent, ProjectVersion, User
from ...security.client_ip import client_ip
from ...security.ratelimit import check_rate_limit
from ..analytics_service import dashboard
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, OptionalUser, load_project, load_version, valid_id
from ..errors import ApiException, clean_validation_errors
from ..quota import DailyQuota, seconds_until_utc_midnight
from ..util import start_of_utc_day
from .shares import resolve_share
from .versions import safe_screenplay

router = APIRouter(tags=["analytics"])

MAX_BODY_BYTES = 32 * 1024
MAX_EVENTS = 100
# Daily event caps (UTC day). Per-IP and per-viewer caps are deliberately far below the share-wide
# cap so one client (rotating viewer ids or not) cannot use up a link's capacity; ``_daily_caps``
# keeps that true even if the share cap is lowered (at most IP_SHARE_PERCENT of it per IP).
SHARE_DAILY_EVENT_CAP = 50_000
IP_DAILY_EVENT_CAP = 20_000
VIEWER_DAILY_EVENT_CAP = 2_000
IP_SHARE_PERCENT = 40
# Requests per minute per client IP and link: this multiple of API_RATE_LIMIT_PER_MINUTE.
IP_REQUESTS_FACTOR = 4
SCENE_ID_PATTERN = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
MAX_SECONDS = 24 * 3600.0


class EventIn(BaseModel):
    """One player event; ``data`` is typed per event (quiz_answer, seek, and pause with only ``{"reason": "blocked"}``
    when the player paused itself because the browser refused playback) and empty otherwise."""

    model_config = ConfigDict(extra="forbid")

    event: str
    scene_id: str | None = Field(default=None, pattern=SCENE_ID_PATTERN)
    t: float = Field(ge=0.0, le=MAX_SECONDS, allow_inf_nan=False)
    scene_t: float | None = Field(default=None, ge=0.0, le=MAX_SECONDS, allow_inf_nan=False)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ANALYTICS_EVENTS:
            raise ValueError(f"unknown event {v!r}")
        return v

    @model_validator(mode="after")
    def _typed_data(self) -> EventIn:
        data = self.data or {}
        if self.event == "quiz_answer":
            choice, correct = data.get("choice"), data.get("correct")
            if not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice <= 9:
                raise ValueError("quiz_answer data.choice must be an integer 0..9")
            if not isinstance(correct, bool):
                raise ValueError("quiz_answer data.correct must be a boolean")
            if self.scene_id is None:
                raise ValueError("quiz_answer requires scene_id")
            self.data = {"choice": choice, "correct": correct}
        elif self.event == "seek":
            values = [data.get("from"), data.get("to")]
            if not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= MAX_SECONDS
                for v in values
            ):
                raise ValueError("seek data needs numeric 'from' and 'to'")
            self.data = {"from": float(values[0]), "to": float(values[1])}
        elif self.event == "pause" and data:
            # the player tags the pause it makes itself when the browser refuses playback (autoplay block)
            if data != {"reason": "blocked"}:
                raise ValueError("pause data may only be {'reason': 'blocked'}")
            self.data = {"reason": "blocked"}
        else:
            if data:
                raise ValueError(f"{self.event} takes no data")
            self.data = {}
        return self


class IngestBody(BaseModel):
    """A batch of player events from one viewer (share token or session + version_id)."""

    model_config = ConfigDict(extra="forbid")

    share_token: str | None = Field(default=None, max_length=64)
    version_id: int | None = None
    viewer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
    events: list[EventIn] = Field(min_length=1, max_length=MAX_EVENTS)


async def _read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiException(413, "too_large", f"Analytics batches are limited to {limit // 1024} KB.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiException(413, "too_large", f"Analytics batches are limited to {limit // 1024} KB.")
        chunks.append(chunk)
    return b"".join(chunks)


def analytics_quota(request: Request) -> DailyQuota:
    """The app's per-IP / per-viewer daily analytics quota (``app.state.analytics_quota``)."""
    quota = getattr(request.app.state, "analytics_quota", None)
    if not isinstance(quota, DailyQuota):
        quota = DailyQuota()
        request.app.state.analytics_quota = quota
    return quota


def _daily_limited(detail: str) -> ApiException:
    return ApiException(429, "rate_limited", detail, headers={"Retry-After": str(seconds_until_utc_midnight())})


def _check_share_cap(db: Session, share_id: int, n_events: int) -> None:
    today = db.execute(
        select(func.count())
        .select_from(AnalyticsEvent)
        .where(AnalyticsEvent.share_link_id == share_id, AnalyticsEvent.created_at >= start_of_utc_day())
    ).scalar_one()
    if today + n_events > SHARE_DAILY_EVENT_CAP:
        raise _daily_limited("Daily analytics limit reached for this link.")


def _daily_caps(shared: bool) -> tuple[int, int]:
    """(per-IP cap, per-viewer cap); for share links both stay below the share-wide cap."""
    ip_cap = IP_DAILY_EVENT_CAP
    if shared:
        ip_cap = min(ip_cap, max(1, SHARE_DAILY_EVENT_CAP * IP_SHARE_PERCENT // 100))
    return ip_cap, min(VIEWER_DAILY_EVENT_CAP, ip_cap)


def _charge_quotas(quota: DailyQuota, scope: str, ip: str, viewer_id: str, n_events: int, *, shared: bool) -> None:
    """Per-IP and per-viewer daily event quotas; 429 for the offending client only."""
    ip_cap, viewer_cap = _daily_caps(shared)
    exhausted = quota.consume(
        [(f"{scope}|ip|{ip}", ip_cap), (f"{scope}|viewer|{viewer_id}", viewer_cap)],
        n_events,
    )
    if exhausted == 0:
        raise _daily_limited("Daily analytics limit reached for this network.")
    if exhausted == 1:
        raise _daily_limited("Daily analytics limit reached for this viewer.")


def _ingest(db: Session, user: User | None, body: IngestBody, settings: Settings, ip: str, quota: DailyQuota) -> None:
    per_minute = settings.api_rate_limit_per_minute
    check_rate_limit("analytics", f"{ip}|{body.viewer_id}", per_minute=per_minute)
    share_id: int | None = None
    user_id: int | None = None
    if body.share_token:
        resolved = resolve_share(db, body.share_token)
        project_id = resolved.project.id
        version_id = resolved.version_id
        share_id = resolved.share.id
        scope = f"share:{share_id}"
    else:
        if user is None:
            raise ApiException(401, "unauthenticated", "Sign in or use a share link.")
        if body.version_id is None:
            raise ApiException(
                422, "validation", [{"loc": ["body", "version_id"], "msg": "version_id is required", "type": "missing"}]
            )
        version: ProjectVersion = load_version(db, user, body.version_id)
        project_id = version.project_id
        version_id = version.id
        user_id = user.id
        scope = f"version:{version.id}"
    # Rotating viewer ids defeats the ip|viewer bucket, so the client IP is limited per link as well.
    check_rate_limit("analytics_ip", f"{scope}|{ip}", per_minute=per_minute * IP_REQUESTS_FACTOR)
    if share_id is not None:
        _check_share_cap(db, share_id, len(body.events))
    _charge_quotas(quota, scope, ip, body.viewer_id, len(body.events), shared=share_id is not None)
    rows = []
    for ev in body.events:
        data = dict(ev.data)
        data["t"] = ev.t
        if ev.scene_t is not None:
            data["scene_t"] = ev.scene_t
        rows.append(
            AnalyticsEvent(
                project_id=project_id,
                version_id=version_id,
                share_link_id=share_id,
                user_id=user_id,
                viewer_id=body.viewer_id,
                event=ev.event,
                scene_id=ev.scene_id,
                data=data,
            )
        )
    db.add_all(rows)
    db.commit()


@router.post("/api/analytics/events", status_code=202)
async def ingest_events(request: Request, db: DbSession, settings: AppSettings, user: OptionalUser) -> Response:
    """Store a batch of player events (share token decides project/version when present)."""
    raw = await _read_limited(request, MAX_BODY_BYTES)
    try:
        body = IngestBody.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        raise ApiException(422, "validation", clean_validation_errors(exc.errors())) from None
    await asyncio.to_thread(
        _ingest,
        db,
        user if not body.share_token else None,
        body,
        settings,
        client_ip(request, settings),
        analytics_quota(request),
    )
    return Response(status_code=202)


@router.get("/api/projects/{project_id}/analytics", dependencies=RATE_LIMITED)
def project_analytics(
    project_id: int, user: CurrentUser, db: DbSession, version_id: int | None = None
) -> dict[str, Any]:
    """Dashboard numbers for a version (default: the project's current version)."""
    project = load_project(db, user, project_id)
    if version_id is not None:
        version = db.get(ProjectVersion, version_id) if valid_id(version_id) else None
        if version is None or version.project_id != project.id:
            raise ApiException(
                422,
                "validation",
                [
                    {
                        "loc": ["query", "version_id"],
                        "msg": "Version does not belong to this project.",
                        "type": "value_error",
                    }
                ],
            )
    else:
        version = db.get(ProjectVersion, project.current_version_id) if project.current_version_id else None
    screenplay = safe_screenplay(version) if version is not None else None
    return dashboard(db, project.id, version.id if version is not None else None, screenplay)
