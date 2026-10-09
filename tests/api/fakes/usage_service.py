"""Fake aadhi.usage.service (aggregates UsageEvent rows)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from aadhi.jobs.base import BudgetExceeded
from aadhi.models import Job, UsageEvent, User


def _today() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(db, user_id: int) -> float:
    total = db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.user_id == user_id, UsageEvent.created_at >= _today()
        )
    ).scalar_one()
    return float(total or 0.0)


def user_daily_budget(user, settings) -> float:
    if user is not None and user.daily_budget_usd is not None:
        return float(user.daily_budget_usd)
    return float(settings.daily_budget_usd_per_user)


def check_budget(db, *, user_id, job_cost_so_far: float, extra_usd: float, settings, job_budget_usd=None) -> None:
    limit = job_budget_usd if job_budget_usd is not None else settings.max_cost_per_lecture_usd
    if job_cost_so_far + extra_usd > limit:
        raise BudgetExceeded("job budget exhausted")
    if user_id is None:
        return
    user = db.get(User, user_id)
    if spent_today(db, user_id) + extra_usd > user_daily_budget(user, settings):
        raise BudgetExceeded("daily budget exhausted")


def usage_for_user(db, user, days: int, settings) -> dict:
    since = _today() - dt.timedelta(days=days - 1)
    rows = db.execute(
        select(
            UsageEvent.created_at, UsageEvent.operation, UsageEvent.provider, UsageEvent.model, UsageEvent.cost_usd
        ).where(UsageEvent.user_id == user.id, UsageEvent.created_at >= since)
    ).all()
    by_day: dict[str, float] = {}
    by_op: dict[str, float] = {}
    by_provider: dict[tuple[str, str], list[float]] = {}
    for created, op, provider, model, cost in rows:
        day = created.date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + cost
        by_op[op] = by_op.get(op, 0.0) + cost
        by_provider.setdefault((provider, model), [0.0, 0])
        by_provider[(provider, model)][0] += cost
        by_provider[(provider, model)][1] += 1
    return {
        "total_usd": round(sum(by_day.values()), 6),
        "today_usd": round(spent_today(db, user.id), 6),
        "daily_budget_usd": user_daily_budget(user, settings),
        "by_day": [{"date": d, "usd": round(v, 6)} for d, v in sorted(by_day.items())],
        "by_operation": [{"operation": o, "usd": round(v, 6)} for o, v in sorted(by_op.items())],
        "by_provider": [
            {"provider": p, "model": m, "usd": round(v[0], 6), "calls": v[1]}
            for (p, m), v in sorted(by_provider.items())
        ],
    }


def usage_for_project(db, project_id: int) -> dict:
    rows = db.execute(
        select(UsageEvent.job_id, Job.kind, Job.created_at, UsageEvent.operation, UsageEvent.cost_usd)
        .join(Job, Job.id == UsageEvent.job_id, isouter=True)
        .where(UsageEvent.project_id == project_id)
    ).all()
    by_job: dict[int, dict] = {}
    by_op: dict[str, float] = {}
    for job_id, kind, created, op, cost in rows:
        if job_id is not None:
            entry = by_job.setdefault(
                job_id,
                {"job_id": job_id, "kind": kind, "usd": 0.0, "created_at": created.isoformat() if created else None},
            )
            entry["usd"] += cost
        by_op[op] = by_op.get(op, 0.0) + cost
    return {
        "total_usd": round(sum(by_op.values()), 6),
        "by_job": sorted(by_job.values(), key=lambda e: e["job_id"]),
        "by_operation": [{"operation": o, "usd": round(v, 6)} for o, v in sorted(by_op.items())],
    }


def usage_admin(db, days: int) -> dict:
    since = _today() - dt.timedelta(days=days - 1)
    users = []
    for user in db.execute(select(User).order_by(User.id)).scalars():
        total = db.execute(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
                UsageEvent.user_id == user.id, UsageEvent.created_at >= since
            )
        ).scalar_one()
        users.append(
            {
                "user_id": user.id,
                "username": user.username,
                "total_usd": float(total),
                "today_usd": spent_today(db, user.id),
            }
        )
    return {"users": users, "total_usd": round(sum(u["total_usd"] for u in users), 6)}
