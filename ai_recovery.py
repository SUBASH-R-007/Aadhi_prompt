"""Recovery manager (Phase 9): continues AI generation runs that nobody is working on any more.

Runs when the server starts and then every AI_RECOVERY_INTERVAL seconds (or at once when work is queued):

  find active runs whose worker's lease expired (or that are queued), backoff over   (one indexed query)
    ─► claim each (atomic; of two managers only one gets a run), at most AI_JOB_CONCURRENCY at a time
    ─► a run interrupted AI_RECOVERY_MAX_ATTEMPTS times stops as needs_attention (no endless loop)
    ─► ai_media.execute(manager=True) classifies and continues it: finalize what is finished, poll the
       provider's job that is still running, fetch a finished one, retry only what is safe to retry,
       stop as needs_attention where a duplicate paid generation could result; transient provider trouble
       defers the run with growing waits (no retry storm after a restart)
  and, at most once an hour, removes old finished runs (AI_RUN_RETENTION_DAYS) and abandoned cache locks.

Nothing here regenerates media by itself: every decision about a run is made from its saved state.
"""
import asyncio
import datetime
import json

import ai_runs
import models
from ai_providers import _env_flag
from ai_runs import RunState
from database import SessionLocal


class RecoveryManager:
    def __init__(self, media, env=None):
        self.media = media
        self.executors = {}  # run kind -> executor (Phase 10: "manim"); anything else is AI media
        self.env = media.env if env is None else env
        self.stats = {"sweeps": 0, "claimed": 0, "recovered": 0, "started": 0, "gave_up": 0, "cleaned": 0}
        self.last_cleanup = None
        self._event = None
        self._loop = None
        media.wake = self.wake

    def register(self, kind, executor):
        """Another kind of durable job (e.g. sandboxed Manim renders) recovered by this same manager."""
        self.executors[kind] = executor
        executor.wake = self.wake

    def executor_for(self, run):
        return self.executors.get(run.kind) or self.media

    def enabled(self):
        return _env_flag(self.env, "AI_RECOVERY_ENABLED", True)

    def wake(self):
        """Queued work exists: sweep now instead of at the next interval."""
        if self._event is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._event.set)

    async def run_forever(self):
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self.media.log("recovery_started", instance=self.media.instance)
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one failed sweep must not stop recovery
                self.media.log("recovery_error", error=str(e))
            try:
                await asyncio.wait_for(self._event.wait(), timeout=ai_runs.settings(self.env)["interval"])
            except asyncio.TimeoutError:
                pass
            self._event.clear()

    def busy(self):
        return sum(1 for executor in [self.media, *self.executors.values()] for task in executor.tasks.values()
                   if getattr(task, "_aadhi_manager", False) and not task.done())

    async def sweep(self):
        """One pass: claims what is due (within the concurrency limit) and starts working on it. Returns the tasks."""
        cfg = ai_runs.settings(self.env)
        self.stats["sweeps"] += 1
        slots = cfg["concurrency"] - self.busy()
        started = []
        db = SessionLocal()
        try:
            if slots > 0:
                for run in ai_runs.due_runs(db, limit=slots * 4):
                    if len(started) >= slots:
                        break
                    if run.id in self.executor_for(run).tasks:
                        continue  # this process works on it already
                    task = self._take(db, run, cfg)
                    if task is not None:
                        started.append(task)
            self._cleanup(db, cfg)
        finally:
            db.close()
        return started

    def _take(self, db, run, cfg):
        owner = ai_runs.worker_token(self.media.instance)
        interrupted = run.status in ai_runs.OWNED  # its worker's lease expired
        attempt = ai_runs.open_attempt(db, run.id) if interrupted else None
        if interrupted and (run.recovery_count or 0) >= cfg["max_recoveries"]:
            if ai_runs.claim(db, run.id, owner, RunState.RECOVERING, [run.status], cfg["lease"]):
                ai_runs.transition(db, run.id, RunState.NEEDS_ATTENTION, from_states=(RunState.RECOVERING,), owner=owner,
                                   finished_at=ai_runs.now(), lease_owner=None, lease_expires_at=None, http_status=409,
                                   error_category="interrupted",
                                   error_message=f"This generation was interrupted {run.recovery_count} times, so Aadhi stopped "
                                                 "trying on its own. Retry or dismiss it.")
                self.stats["gave_up"] += 1
                self.media.log("recovery_gave_up", run=run.id, recoveries=run.recovery_count)
            return None
        if run.status == RunState.CANCEL_REQUESTED:
            to_state = RunState.CANCEL_REQUESTED
        else:
            to_state = RunState.RECOVERING if interrupted else RunState.RUNNING
        fields = {"recovery_count": (run.recovery_count or 0) + 1} if interrupted else {}
        if not ai_runs.claim(db, run.id, owner, to_state, [run.status], cfg["lease"], **fields):
            return None  # another worker claimed it first
        self.stats["claimed"] += 1
        if interrupted:
            self.stats["recovered"] += 1
        else:
            self.stats["started"] += 1
        self.media.log("recovery_claim" if interrupted else "queue_claim", run=run.id, previous=run.status,
                       attempt_state=attempt.state if attempt else None, job=attempt.provider_job_id if attempt else None,
                       recoveries=fields.get("recovery_count"))
        task = self.executor_for(run).spawn(run.id, owner, manager=True)
        task._aadhi_manager = True
        return task

    def _cleanup(self, db, cfg):
        now = ai_runs.now()
        if self.last_cleanup and now - self.last_cleanup < datetime.timedelta(hours=1):
            return
        self.last_cleanup = now
        cutoff = now - datetime.timedelta(days=cfg["retention_days"])
        old = [r.id for r in db.query(models.AIGenerationRun.id).filter(
            models.AIGenerationRun.status.in_([RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED]),
            models.AIGenerationRun.finished_at.isnot(None), models.AIGenerationRun.finished_at < cutoff).limit(500)]
        if old:  # the assets keep their provenance in their own details; only the run history goes
            db.query(models.AIGenerationAttempt).filter(models.AIGenerationAttempt.run_id.in_(old)).delete(synchronize_session=False)
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id.in_(old)).delete(synchronize_session=False)
            db.commit()
        locks = self.media.cache.sweep_locks(db)
        for executor in self.executors.values():  # e.g. sandbox workspaces left by a crash
            sweep = getattr(executor, "sweep", None)
            if sweep:
                sweep(db)
        self.stats["cleaned"] += len(old)
        if old or locks:
            self.media.log("cleanup", runs=len(old), locks=locks)

    def summary(self, db, scope_key=None):
        """Counts for the providers panel: active, recovering, needing attention."""
        query = db.query(models.AIGenerationRun.status, models.AIGenerationRun.recovery_count)
        if scope_key:
            query = query.filter(models.AIGenerationRun.scope_key == scope_key)
        rows = query.filter(models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE) + [RunState.NEEDS_ATTENTION])).all()
        return {"active": sum(1 for s, _ in rows if s in ai_runs.ACTIVE),
                "recovering": sum(1 for s, _ in rows if s == RunState.RECOVERING),
                "needs_attention": sum(1 for s, _ in rows if s == RunState.NEEDS_ATTENTION),
                "manager": dict(self.stats), "enabled": self.enabled()}
