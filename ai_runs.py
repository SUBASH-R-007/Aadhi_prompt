"""Durable AI generation runs (Phase 9): the state machine, worker leases and attempt history that let an
interrupted generation be recovered instead of started again.

A run (models.AIGenerationRun) is one logical request ("an AI video for scene 4 of lesson 12"); each try
with one provider is an attempt (models.AIGenerationAttempt), kept as history. Every state change is a
guarded UPDATE: it only succeeds from the expected states and, while a worker owns the run, only for that
worker (its lease). Two workers can therefore never both finish a run, and a worker that lost its lease
(it stalled and recovery took over) cannot overwrite the new owner's work.

Run states                                          (who moves it)
  queued            waiting for a worker            request, batch, recovery (deferred), user retry
  running           a worker owns it (lease)        worker
  recovering        a recovery worker took it over  recovery (after the previous lease expired)
  cancel_requested  the user asked to stop          user; the owner (or recovery) finishes the cancel
  completed         usable media is a library asset worker / recovery                       terminal
  failed            nothing usable could be made    worker / recovery                       terminal
  cancelled         stopped on request              worker / recovery / user (while queued) terminal
  needs_attention   stopped because continuing might duplicate a paid generation or the
                    provider's state is unknown; a person decides (retry or dismiss)        terminal until then

Attempt states
  starting     about to call the provider (a crash here leaves it unknown whether the provider got it)
  submitted    the provider accepted a job; its id and handle are saved (recovery polls the same job)
  downloading  the job finished; its output is being fetched
  registering  the output is on disk (output_path) and being validated / added to the library
  completed    the attempt produced the run's asset
  failed       the provider failed (category says whether another provider may be tried)
  lost         the provider's job is gone, or the process died where nothing was billed
  ambiguous    the provider may have accepted a paid request that cannot be found again
  cancelled    stopped on request
"""
import datetime
import json
import os
import uuid

from sqlalchemy import or_

import models
from ai_providers import MediaRequest, _env_float


class RunState:
    QUEUED, RUNNING, RECOVERING, CANCEL_REQUESTED = "queued", "running", "recovering", "cancel_requested"
    COMPLETED, FAILED, CANCELLED, NEEDS_ATTENTION = "completed", "failed", "cancelled", "needs_attention"


ACTIVE = (RunState.QUEUED, RunState.RUNNING, RunState.RECOVERING, RunState.CANCEL_REQUESTED)
TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.NEEDS_ATTENTION)
OWNED = (RunState.RUNNING, RunState.RECOVERING, RunState.CANCEL_REQUESTED)  # a worker holds the lease

# Allowed transitions: from -> to
TRANSITIONS = {
    RunState.QUEUED: {RunState.RUNNING, RunState.RECOVERING, RunState.CANCELLED, RunState.COMPLETED, RunState.FAILED},
    RunState.RUNNING: {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.NEEDS_ATTENTION,
                       RunState.CANCEL_REQUESTED, RunState.RECOVERING, RunState.QUEUED},
    RunState.RECOVERING: {RunState.RUNNING, RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED,
                          RunState.NEEDS_ATTENTION, RunState.CANCEL_REQUESTED, RunState.QUEUED, RunState.RECOVERING},
    RunState.CANCEL_REQUESTED: {RunState.CANCELLED, RunState.COMPLETED, RunState.FAILED, RunState.NEEDS_ATTENTION,
                                RunState.RECOVERING},
    RunState.NEEDS_ATTENTION: {RunState.QUEUED, RunState.CANCELLED},  # a person decides: retry or dismiss
    RunState.FAILED: set(),
    RunState.COMPLETED: set(),
    RunState.CANCELLED: set(),
}


class AttemptState:
    STARTING, SUBMITTED, DOWNLOADING, REGISTERING = "starting", "submitted", "downloading", "registering"
    RENDERING = "rendering"  # Phase 10: a sandboxed Manim render is running (it dies with its server; recovery renders again)
    COMPLETED, FAILED, LOST, AMBIGUOUS, CANCELLED = "completed", "failed", "lost", "ambiguous", "cancelled"


ATTEMPT_OPEN = (AttemptState.STARTING, AttemptState.SUBMITTED, AttemptState.DOWNLOADING, AttemptState.REGISTERING,
                AttemptState.RENDERING)


class LeaseLost(Exception):
    """This worker no longer owns the run (another worker took it over): it must stop writing."""


def now():
    return datetime.datetime.utcnow()


def settings(env=None):
    env = os.environ if env is None else env
    return {"lease": _env_float(env, "AI_JOB_LEASE_SECONDS", 60), "heartbeat": _env_float(env, "AI_JOB_HEARTBEAT_SECONDS", 15),
            "interval": _env_float(env, "AI_RECOVERY_INTERVAL", 15), "concurrency": max(1, int(_env_float(env, "AI_JOB_CONCURRENCY", 2))),
            "max_recoveries": max(1, int(_env_float(env, "AI_RECOVERY_MAX_ATTEMPTS", 3))),
            "retention_days": _env_float(env, "AI_RUN_RETENTION_DAYS", 90)}


def worker_token(instance):
    return f"{instance}:{uuid.uuid4().hex[:10]}"


# ---- requests -------------------------------------------------------------------------------------------

REQUEST_FIELDS = ("media_type", "prompt", "provider", "allow_fallback", "model", "aspect_ratio", "duration_seconds",
                  "force_regenerate", "purpose", "project_id", "scene_index", "slot", "presenter")


def request_json(request):
    return json.dumps({k: getattr(request, k) for k in REQUEST_FIELDS}, sort_keys=True)


def request_from(run):
    data = json.loads(run.request or "{}")
    return MediaRequest(**{k: data.get(k) for k in REQUEST_FIELDS if k in data})


# ---- guarded state changes ------------------------------------------------------------------------------

def transition(db, run_id, to_state, *, from_states, owner=None, **fields):
    """Moves the run to `to_state` only if it is in one of `from_states` (and, with `owner`, still owned by
    that worker). Atomic (one conditional UPDATE). Returns True when this call made the change."""
    for state in from_states:
        assert to_state in TRANSITIONS[state] or to_state == state, (state, to_state)
    query = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id,
                                                    models.AIGenerationRun.status.in_(list(from_states)))
    if owner is not None:
        query = query.filter(models.AIGenerationRun.lease_owner == owner)
    changed = query.update({**fields, "status": to_state}, synchronize_session=False)
    db.commit()
    return changed == 1


def update_owned(db, run_id, owner, **fields):
    """Writes fields of a run this worker owns; raises LeaseLost when it does not own it any more."""
    changed = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id,
                                                      models.AIGenerationRun.lease_owner == owner).update(
        fields, synchronize_session=False)
    db.commit()
    if changed != 1:
        raise LeaseLost(run_id)


def claim(db, run_id, owner, to_state, from_states, lease_seconds, **fields):
    """Takes ownership of a run nobody owns (or whose lease expired). Atomic: of two workers claiming the
    same run, exactly one succeeds."""
    t = now()
    query = db.query(models.AIGenerationRun).filter(
        models.AIGenerationRun.id == run_id, models.AIGenerationRun.status.in_(list(from_states)),
        or_(models.AIGenerationRun.lease_owner.is_(None), models.AIGenerationRun.lease_expires_at < t))
    changed = query.update({**fields, "status": to_state, "lease_owner": owner,
                            "lease_expires_at": t + datetime.timedelta(seconds=lease_seconds), "heartbeat_at": t},
                           synchronize_session=False)
    db.commit()
    return changed == 1


def renew(db, run_id, owner, lease_seconds):
    """The heartbeat: extends this worker's lease. False when the lease was lost."""
    t = now()
    changed = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id,
                                                      models.AIGenerationRun.lease_owner == owner).update(
        {"lease_expires_at": t + datetime.timedelta(seconds=lease_seconds), "heartbeat_at": t}, synchronize_session=False)
    db.commit()
    return changed == 1


def release(db, run_id, owner):
    db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id,
                                            models.AIGenerationRun.lease_owner == owner).update(
        {"lease_owner": None, "lease_expires_at": None}, synchronize_session=False)
    db.commit()


# ---- attempts ---------------------------------------------------------------------------------------------

def attempts_of(db, run_id):
    return (db.query(models.AIGenerationAttempt).filter(models.AIGenerationAttempt.run_id == run_id)
            .order_by(models.AIGenerationAttempt.number).all())


def open_attempt(db, run_id):
    rows = attempts_of(db, run_id)
    return rows[-1] if rows and rows[-1].state in ATTEMPT_OPEN else None


def new_attempt(db, run_id, owner, provider, model, generation_hash):
    number = (db.query(models.AIGenerationAttempt).filter(models.AIGenerationAttempt.run_id == run_id).count()) + 1
    attempt = models.AIGenerationAttempt(id=uuid.uuid4().hex, run_id=run_id, number=number, provider=provider, model=model,
                                         generation_hash=generation_hash, state=AttemptState.STARTING, owner=owner,
                                         started_at=now())
    db.add(attempt)
    db.commit()
    return attempt


def set_attempt(db, attempt, detail_update=None, **fields):
    for key, value in fields.items():
        setattr(attempt, key, value)
    if detail_update:
        data = json.loads(attempt.detail or "{}")
        data.update(detail_update)
        attempt.detail = json.dumps(data)
    db.commit()


def attempt_view(attempt):
    detail = json.loads(attempt.detail or "{}")
    return {"attempt_id": attempt.id, "number": attempt.number, "provider": attempt.provider, "model": attempt.model,
            "state": attempt.state, "provider_job_id": attempt.provider_job_id, "error_category": attempt.error_category,
            "error_message": attempt.error_message, "recovered": attempt.recovered, "asset_id": attempt.asset_id,
            "tries": detail.get("tries"), "provider_cancel": detail.get("provider_cancel"),
            "started_at": _iso(attempt.started_at), "submitted_at": _iso(attempt.submitted_at),
            "finished_at": _iso(attempt.finished_at)}


def _iso(value):
    return value.isoformat() + "Z" if value else None


# ---- heartbeat (shared by every executor) ---------------------------------------------------------------------

async def heartbeat(run_id, owner, task, flags, env=None):
    """Renews the worker's lease; stops the worker (task.cancel) when the lease was lost (flags[run_id] =
    "lease") or the user asked to cancel from anywhere (flags[run_id] = "user")."""
    import asyncio
    from database import SessionLocal
    cfg = settings(env)
    while True:
        await asyncio.sleep(cfg["heartbeat"])
        db = SessionLocal()
        try:
            if not renew(db, run_id, owner, cfg["lease"]):
                flags[run_id] = "lease"
                task.cancel()
                return
            status = db.query(models.AIGenerationRun.status).filter(models.AIGenerationRun.id == run_id).scalar()
            if status == RunState.CANCEL_REQUESTED and run_id not in flags:
                flags[run_id] = "user"
                task.cancel()
                return
        except Exception:  # noqa: BLE001 - a missed heartbeat only shortens the lease
            db.rollback()
        finally:
            db.close()


# ---- queries the recovery manager uses (indexed) ------------------------------------------------------------

def due_runs(db, limit=50):
    """Active runs nobody owns (or whose owner's lease expired) and whose backoff is over."""
    t = now()
    return (db.query(models.AIGenerationRun)
            .filter(models.AIGenerationRun.status.in_(list(ACTIVE)),
                    or_(models.AIGenerationRun.lease_owner.is_(None), models.AIGenerationRun.lease_expires_at < t),
                    or_(models.AIGenerationRun.not_before.is_(None), models.AIGenerationRun.not_before <= t))
            .order_by(models.AIGenerationRun.created_at).limit(limit).all())
