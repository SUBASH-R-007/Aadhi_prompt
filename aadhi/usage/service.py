"""Usage recording, budgets and summaries (all sums are SQL aggregates).

Days are UTC calendar days: a user's daily budget resets at 00:00 UTC (05:30 IST).

``UsageEvent.billed_to`` is ``"user"`` for usage paid with the user's personal API key
(``aadhi.credentials``): it is reported (``own_key_usd``) but never counted against the daily budget.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..jobs.base import BudgetExceeded
from ..models import Job, UsageEvent, User, utcnow
from ..providers.base import Usage

__all__ = [
    "check_budget",
    "record_usage",
    "spent_today",
    "usage_admin",
    "usage_for_project",
    "usage_for_user",
    "user_daily_budget",
]

_EPS = 1e-9
MAX_DAYS = 366
BILLED_TO = ("server", "user")


def _start_of_day(now: dt.datetime | None = None) -> dt.datetime:
    now = now or utcnow()
    return now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _since(days: int) -> dt.datetime:
    days = max(1, min(int(days), MAX_DAYS))
    return _start_of_day() - dt.timedelta(days=days - 1)


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # SQLite returns naive datetimes (stored as UTC)
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def _usd(value: Any) -> float:
    return round(float(value or 0.0), 6)


def record_usage(
    db: Session,
    usage: Usage,
    *,
    user_id: int | None,
    project_id: int | None,
    job_id: int | None,
    cost_usd: float,
    billed_to: str = "server",
) -> UsageEvent:
    """Insert one ``UsageEvent`` (flushed, caller commits). ``billed_to="user"``: paid with a personal key."""
    event = UsageEvent(
        user_id=user_id,
        project_id=project_id,
        job_id=job_id,
        provider=(usage.provider or "unknown")[:32],
        model=(usage.model or "")[:128],
        operation=(usage.operation or "unknown")[:32],
        input_tokens=max(int(usage.input_tokens or 0), 0),
        output_tokens=max(int(usage.output_tokens or 0), 0),
        characters=max(int(usage.characters or 0), 0),
        seconds=max(float(usage.seconds or 0.0), 0.0),
        units=max(int(usage.units or 0), 0),
        cost_usd=max(float(cost_usd or 0.0), 0.0),
        billed_to=billed_to if billed_to in BILLED_TO else "server",
        meta=dict(usage.meta or {}),
    )
    db.add(event)
    db.flush()
    return event


def spent_today(db: Session, user_id: int) -> float:
    """USD billed to the server for ``user_id`` since 00:00 UTC today (the daily budget's measure;
    usage paid with the user's own API key is excluded)."""
    total = db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= _start_of_day(),
            UsageEvent.billed_to == "server",
        )
    ).scalar_one()
    return _usd(total)


def user_daily_budget(user: User | None, settings: Settings) -> float:
    """The user's daily budget (``User.daily_budget_usd`` or ``DAILY_BUDGET_USD_PER_USER``)."""
    if user is not None and user.daily_budget_usd is not None:
        return max(float(user.daily_budget_usd), 0.0)
    return max(float(settings.daily_budget_usd_per_user), 0.0)


def check_budget(
    db: Session,
    *,
    user_id: int | None,
    job_cost_so_far: float,
    extra_usd: float,
    settings: Settings,
    job_budget_usd: float | None = None,
) -> None:
    """Raise ``BudgetExceeded`` if spending ``extra_usd`` more breaks the job or daily user budget.

    Job limit: ``job_budget_usd`` or ``MAX_COST_PER_LECTURE_USD``. User limit: today's server-billed
    spend (``spent_today``) + ``extra_usd`` must stay within ``user_daily_budget``.
    """
    extra = max(float(extra_usd or 0.0), 0.0)
    job_limit = float(settings.max_cost_per_lecture_usd if job_budget_usd is None else job_budget_usd)
    job_total = max(float(job_cost_so_far or 0.0), 0.0) + extra
    if job_total > job_limit + _EPS:
        raise BudgetExceeded(f"This job would cost about ${job_total:.2f}, above its budget of ${job_limit:.2f}")
    if user_id is None:
        return
    user = db.get(User, user_id)
    budget = user_daily_budget(user, settings)
    spent = spent_today(db, user_id)
    if spent + extra > budget + _EPS:
        raise BudgetExceeded(
            f"Daily budget reached: ${spent:.2f} of ${budget:.2f} spent today (UTC); try again tomorrow "
            "or ask an admin to raise your budget"
        )


def _by_operation(db: Session, *conds: Any) -> list[dict[str, Any]]:
    rows = db.execute(
        select(UsageEvent.operation, func.sum(UsageEvent.cost_usd))
        .where(*conds)
        .group_by(UsageEvent.operation)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
    ).all()
    return [{"operation": op, "usd": _usd(usd)} for op, usd in rows]


def usage_for_user(db: Session, user: User, days: int, settings: Settings) -> dict[str, Any]:
    """``GET /api/usage/me`` payload for the last ``days`` UTC days (1..366).

    ``total_usd`` counts everything; ``own_key_usd`` is the part paid with the user's personal API
    keys over the same period; ``today_usd`` is today's server-billed spend (what the daily budget
    measures).
    """
    since = _since(days)
    conds = (UsageEvent.user_id == user.id, UsageEvent.created_at >= since)
    total = db.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(*conds)).scalar_one()
    own_key = db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(*conds, UsageEvent.billed_to == "user")
    ).scalar_one()
    day = func.date(UsageEvent.created_at)
    by_day = db.execute(select(day, func.sum(UsageEvent.cost_usd)).where(*conds).group_by(day).order_by(day)).all()
    by_provider = db.execute(
        select(UsageEvent.provider, UsageEvent.model, func.sum(UsageEvent.cost_usd), func.count(UsageEvent.id))
        .where(*conds)
        .group_by(UsageEvent.provider, UsageEvent.model)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
    ).all()
    return {
        "total_usd": _usd(total),
        "own_key_usd": _usd(own_key),
        "today_usd": spent_today(db, user.id),
        "daily_budget_usd": user_daily_budget(user, settings),
        "by_day": [{"date": str(d), "usd": _usd(usd)} for d, usd in by_day],
        "by_operation": _by_operation(db, *conds),
        "by_provider": [
            {"provider": p, "model": m, "usd": _usd(usd), "calls": int(calls)} for p, m, usd, calls in by_provider
        ],
    }


def usage_for_project(db: Session, project_id: int) -> dict[str, Any]:
    """``GET /api/usage/projects/{id}`` payload (all time)."""
    cond = UsageEvent.project_id == project_id
    total = db.execute(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(cond)).scalar_one()
    rows = db.execute(
        select(UsageEvent.job_id, Job.kind, func.sum(UsageEvent.cost_usd), Job.created_at)
        .join(Job, Job.id == UsageEvent.job_id, isouter=True)
        .where(cond)
        .group_by(UsageEvent.job_id, Job.kind, Job.created_at)
        .order_by(Job.created_at.desc())
    ).all()
    by_job = [
        {
            "job_id": job_id,
            "kind": kind,
            "usd": _usd(usd),
            "created_at": _iso(created),
        }
        for job_id, kind, usd, created in rows
    ]
    return {"total_usd": _usd(total), "by_job": by_job, "by_operation": _by_operation(db, cond)}


def usage_admin(db: Session, days: int) -> dict[str, Any]:
    """``GET /api/admin/usage`` payload: per-user totals for the last ``days`` days + today.

    ``total_usd`` counts everything; ``own_key_usd`` is the part paid with the users' personal API
    keys (``billed_to='user'``), i.e. not paid by the server; ``today_usd`` is today's server-billed
    spend (what the daily budget measures, as in ``/api/usage/me``).
    """
    since = _since(days)
    today = _start_of_day()
    total_col = func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)
    today_server = (UsageEvent.created_at >= today) & (UsageEvent.billed_to == "server")
    today_col = func.coalesce(func.sum(case((today_server, UsageEvent.cost_usd), else_=0.0)), 0.0)
    own_col = func.coalesce(func.sum(case((UsageEvent.billed_to == "user", UsageEvent.cost_usd), else_=0.0)), 0.0)
    rows = db.execute(
        select(User.id, User.username, total_col, today_col, own_col)
        .join(UsageEvent, (UsageEvent.user_id == User.id) & (UsageEvent.created_at >= since), isouter=True)
        .group_by(User.id, User.username)
        .order_by(total_col.desc(), User.username)
    ).all()
    users = [
        {
            "user_id": uid,
            "username": name,
            "total_usd": _usd(total),
            "today_usd": _usd(today_usd),
            "own_key_usd": _usd(own_key),
        }
        for uid, name, total, today_usd, own_key in rows
    ]
    grand, grand_own = db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0), own_col).where(UsageEvent.created_at >= since)
    ).one()
    return {"users": users, "total_usd": _usd(grand), "own_key_usd": _usd(grand_own)}
