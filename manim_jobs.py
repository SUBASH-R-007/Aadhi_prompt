"""Manim renders as durable jobs (Phase 10), on the Phase 9 run / attempt / lease model.

  request (code + render profile [+ lesson, scene, slot])
    ─► static checks (manim_security: early rejection; the sandbox is the boundary)
    ─► secure runtime available? (manim_sandbox; otherwise "unavailable" — never an unsafe fallback)
    ─► cache: the same code + Manim version + profile rendered before → that asset (also the old pipeline's files)
    ─► a run (kind "manim"; an identical active request attaches to it), owned under a lease
    ─► attempt: starting → rendering (sandbox) → registering (output validated, placed) → completed (asset)
    ─► library asset (Phase 3, idempotent) ─► cache entry (Phase 5, once) ─► run completed ─► lesson plan

Checkpoints are the safe boundaries only: a render cut off midway (server crash: the sandbox dies with its
server) is rendered again from the start; a finished render is registered, a registered asset finalized —
nothing is rendered twice and no asset is duplicated. Visual Review decides first for work continued
without the user. Cancelling stops the sandbox (its whole process tree).
"""
import asyncio
import datetime
import hashlib
import json
import os
import shutil
import threading
import time
import uuid

import ai_runs
import models
import manim_security as security
from ai_cache import generation_hash
from ai_media import GenerationFailed, Outcome
from ai_runs import AttemptState, LeaseLost, RunState
from database import SessionLocal
from manim_sandbox import SandboxUnavailable, message_for
from media import MediaError, inspect_media

# HTTP answers by failure category
STATUS = {"unsafe_code": 422, "invalid_code": 422, "source_limit": 413, "scene_limit": 422, "resolution_limit": 422,
          "fps_limit": 422, "frame_limit": 422, "memory_limit": 422, "cpu_limit": 422, "output_limit": 422, "timeout": 504,
          "render_failed": 422, "invalid_output": 502, "filesystem_access_denied": 422, "network_denied": 422,
          "network_access_denied": 422, "process_denied": 422, "process_limit": 422, "cancelled": 499,
          "sandbox_unavailable": 503, "sandbox_error": 500, "busy": 429, "not_wanted": 409}


def _now():
    return datetime.datetime.utcnow()


def _int(env, name, default):
    try:
        return int(float(env.get(name) or default))
    except ValueError:
        return default


class ManimFailed(GenerationFailed):
    def __init__(self, category, message=None, detail=""):
        super().__init__(STATUS.get(category, 500), message or message_for(category), category=category)
        self.detail = detail


class ManimService:
    kind = "manim"

    def __init__(self, library, cache, sandbox, *, static_dir, env=None, log=None):
        self.library = library
        self.cache = cache
        self.sandbox = sandbox
        self.static_dir = static_dir
        self.env = os.environ if env is None else env
        self.log_enabled = (self.env.get("AI_MEDIA_LOG", "1").strip().lower() not in ("0", "false", "no", "off")) if log is None else log
        self.instance = uuid.uuid4().hex[:12]
        self.tasks = {}
        self.active = set()
        self.cancel_flags = {}
        self.stats = {"renders": 0, "cache_hits": 0, "legacy_reused": 0, "rejected": 0, "failed": 0, "recovered": 0, "attached": 0}
        self.slots = threading.BoundedSemaphore(max(1, _int(self.env, "MANIM_MAX_CONCURRENT", 1)))
        self._lock = threading.Lock()
        self.rendering = {}  # user id -> sandboxes running for that user
        self.is_wanted = None     # callback(db, run) -> (bool, why): Visual Review still wants this render (server.py)
        self.on_completed = None  # callback(db, run): the lesson that asked for it is updated
        self.wake = None

    # ---- observability ------------------------------------------------------------------------------------

    def log(self, event, **fields):
        if self.log_enabled:
            clean = {k: v for k, v in fields.items() if v is not None}
            print("[MANIM] " + json.dumps({"event": event, **clean}, sort_keys=True, default=str))

    def _crash(self, point):
        """Test-only crash points (MANIM_TEST_CRASH_AT, only on a test server with AI_FAKE_PROVIDER=1)."""
        if self.env.get("AI_FAKE_PROVIDER") == "1" and self.env.get("MANIM_TEST_CRASH_AT") == point:
            print(f"[MANIM] simulated crash at {point}", flush=True)
            os._exit(97)

    # ---- the request ----------------------------------------------------------------------------------------

    def prepare(self, code, profile):
        """Checks and normalizes a render request: (code, scene, limits, fingerprint, identity). Raises ManimFailed."""
        try:
            lim = security.limits(profile or security.DEFAULT_PROFILE, self.env)
        except ValueError:
            raise ManimFailed("invalid_code", "Unknown render profile; use preview, standard or high_quality.")
        fixed = security.fix_code(code)
        try:
            scenes = security.check_source(fixed, lim)
        except security.UnsafeCode as e:
            self.stats["rejected"] += 1
            self.log("rejected", category=e.category, detail=e.detail)
            raise ManimFailed(e.category, e.message, e.detail)
        scene = security.scene_name(fixed, scenes)
        fp, fp_data = security.fingerprint(fixed, lim, scene)
        identity = {"v": 1, "media_type": "video", "provider": "manim", "model": f"manim {security.MANIM_VERSION}",
                    "prompt": f"manim:{scene}:{fp_data['code_sha256']}",
                    "parameters": {k: fp_data[k] for k in ("width", "height", "fps", "background", "scene")}}
        return {"code": fixed, "scene": scene, "limits": lim, "fingerprint": fp, "identity": identity,
                "hash": generation_hash(identity)}

    def cached(self, db, user_id, code, profile=security.DEFAULT_PROFILE):
        """The asset of an earlier render of exactly this request, or None (never renders). For the visual router."""
        try:
            req = self.prepare(code, profile)
        except ManimFailed:
            return None
        hit = self.cache.peek(db, user_id, req["identity"], req["hash"])
        return hit.asset if hit else None

    def _legacy(self, db, user, req):
        """The old /render pipeline kept its videos as static/<Scene>_<sha256(code)[:16]>.mp4 (720p30 = standard):
        such a video is the same render, so it is reused (and recorded in the cache) instead of rendered again."""
        if req["limits"]["profile"] != "standard":
            return None
        name = f"{req['scene']}_{hashlib.sha256(req['code'].encode('utf-8')).hexdigest()[:16]}.mp4"
        if not os.path.isfile(os.path.join(self.static_dir, name)):
            return None
        try:
            asset = self.library.adopt(db, "static", name, owner_id=user.id, source="manim", file_name=name,
                                       details={"scene": req["scene"], "generation": {"hash": req["hash"], "provider": "manim",
                                                                                      "model": req["identity"]["model"], "legacy": True}})
        except MediaError:
            return None
        self.cache.record_once(db, f"user:{user.id}", asset, req["identity"], req["hash"])
        self.stats["legacy_reused"] += 1
        return asset

    # ---- entry points ----------------------------------------------------------------------------------------

    async def render(self, db, user, code, profile=None, project_id=None, scene_index=None, slot=None, force=False, wait=True,
                     scene_id=None):
        """A rendered animation for this code: from the cache when it was rendered before, otherwise rendered in the
        sandbox (waiting, or in the background with wait=False → (None, run)). Raises ManimFailed."""
        req = self.prepare(code, profile)
        try:
            self.sandbox.runtime()
        except SandboxUnavailable as e:
            self.log("unavailable", reason=str(e))
            raise ManimFailed("sandbox_unavailable")
        if not force:
            hit = self.cache.lookup(db, user.id, req["identity"], req["hash"])
            asset = hit.asset if hit else self._legacy(db, user, req)
            if asset is not None:
                self.stats["cache_hits"] += 1
                outcome = Outcome(asset=asset, cache_hit=True, generated=False, provider="manim", model=req["identity"]["model"])
                return outcome if wait else (outcome, None)
        request = {"media_type": "video", "prompt": req["code"], "profile": req["limits"]["profile"], "scene": req["scene"],
                   "fingerprint": req["fingerprint"], "force_regenerate": bool(force), "project_id": project_id,
                   "scene_index": scene_index, "slot": slot, "purpose": "manim"}
        if scene_id:  # Phase 20: the scene's id, so the finished render finds its scene after it moved (visuals.run_request)
            request["scene_id"] = scene_id
        owner = ai_runs.worker_token(self.instance)
        run, attached = self._open_run(db, user, request, owner if wait else None, wait)
        if attached:
            self.stats["attached"] += 1
            if not wait:
                return None, run
            return await self._wait_for(db, run.id)
        if not wait:
            owner = ai_runs.worker_token(self.instance)
            if ai_runs.claim(db, run.id, owner, RunState.RUNNING, [RunState.QUEUED], self.cfg()["lease"]):
                self.spawn(run.id, owner)
            elif self.wake:
                self.wake()  # recovery claimed it first: it renders it
            return None, run
        return await self.execute(run.id, owner, db=db)

    def cfg(self):
        return ai_runs.settings(self.env)

    def _open_run(self, db, user, request, owner, wait):
        # The same animation requested again while it renders (the preview and the export, two tabs) waits for that
        # render instead of starting another: one render, one asset
        digest = hashlib.sha256(json.dumps({"scope": user.id, "kind": self.kind, "fp": request["fingerprint"],
                                            "force": request["force_regenerate"]}, sort_keys=True).encode()).hexdigest()
        existing = (db.query(models.AIGenerationRun)
                    .filter(models.AIGenerationRun.scope_key == f"user:{user.id}", models.AIGenerationRun.request_hash == digest,
                            models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).first())
        if existing:
            return existing, True
        active = db.query(models.AIGenerationRun.scope_key).filter(models.AIGenerationRun.kind == self.kind,
                                                                   models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).all()
        mine = sum(1 for (scope,) in active if scope == f"user:{user.id}")
        if mine >= _int(self.env, "MANIM_MAX_QUEUED_PER_USER", 10):
            raise ManimFailed("busy", f"You already have {mine} animations waiting or rendering; wait for them to finish.")
        if len(active) >= _int(self.env, "MANIM_MAX_QUEUED", 50):
            raise ManimFailed("busy", "Too many animations are waiting to render on this server; try again in a few minutes.")
        t = _now()
        run = models.AIGenerationRun(
            id=uuid.uuid4().hex, scope_key=f"user:{user.id}", user_id=user.id, media_type="video", kind=self.kind,
            status=RunState.RUNNING if owner else RunState.QUEUED, requested_provider="manim", forced=bool(request["force_regenerate"]),
            instance=self.instance, created_at=t, heartbeat_at=t, request=json.dumps(request), request_hash=digest,
            project_id=request["project_id"], scene_index=request["scene_index"], slot=request["slot"], recovery_count=0,
            allow_duplicate=False, lease_owner=owner, attempts=0,
            lease_expires_at=t + datetime.timedelta(seconds=self.cfg()["lease"]) if owner else None,
            detail=json.dumps({"background": not wait, "purpose": "manim", "profile": request["profile"], "attempts": []}))
        db.add(run)
        db.commit()
        return run, False

    async def _wait_for(self, db, run_id, timeout=1800):
        deadline = time.time() + timeout
        while time.time() < deadline:
            db.expire_all()
            run = db.get(models.AIGenerationRun, run_id)
            if run.status in ai_runs.TERMINAL:
                if run.status == RunState.COMPLETED and run.asset_id:
                    asset = db.get(models.Asset, run.asset_id)
                    return Outcome(asset=asset, cache_hit=True, generated=False, provider="manim", model=run.model, run_id=run.id)
                raise ManimFailed(run.error_category or "sandbox_error", run.error_message)
            await asyncio.sleep(0.25)
        raise ManimFailed("timeout")

    def spawn(self, run_id, owner, manager=False):
        task = asyncio.get_running_loop().create_task(self.execute(run_id, owner, manager=manager, quiet=True))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self.tasks.pop(rid, None))
        return task

    # ---- execution --------------------------------------------------------------------------------------------

    async def execute(self, run_id, owner, manager=False, quiet=False, db=None):
        own_db = db is None
        db = db or SessionLocal()
        me = asyncio.current_task()
        self.tasks.setdefault(run_id, me)
        self.active.add(run_id)
        heartbeat = asyncio.ensure_future(ai_runs.heartbeat(run_id, owner, me, self.cancel_flags, self.env))
        try:
            run = db.get(models.AIGenerationRun, run_id)
            user = db.get(models.User, run.user_id)
            request = json.loads(run.request or "{}")
            try:
                return await self._execute(db, user, request, run, owner, manager)
            except GenerationFailed as failure:
                self._settle(db, run, owner, failure)
                if quiet:
                    return None
                raise
        except LeaseLost:
            self.log("lease_lost", run=run_id)
            if quiet:
                return None
            raise ManimFailed("sandbox_error", "Another worker took over this animation.")
        except asyncio.CancelledError:
            why = self.cancel_flags.get(run_id)
            db.rollback()
            if why == "user":
                try:
                    self._finish(db, run_id, owner, RunState.CANCELLED, error_category="cancelled", http_status=499,
                                 error_message=message_for("cancelled"))
                except LeaseLost:
                    pass
            elif why != "lease":
                db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id,
                                                        models.AIGenerationRun.lease_owner == owner).update(
                    {"lease_expires_at": _now()}, synchronize_session=False)  # server stopping: recovery takes it at once
                db.commit()
            if quiet:
                return None
            if why in ("user", "lease") and me is not None:
                # Stopped by this service (the user cancelled, or another worker took over), not by the caller: the
                # request that waits for the render gets an answer instead of being cancelled itself
                me.uncancel()
                if why == "user":
                    raise ManimFailed("cancelled")
                raise ManimFailed("sandbox_error", "Another worker took over this animation.")
            raise
        finally:
            heartbeat.cancel()
            self.cancel_flags.pop(run_id, None)
            self.active.discard(run_id)
            if self.tasks.get(run_id) is me:
                self.tasks.pop(run_id, None)
            if own_db:
                db.close()

    def _finish(self, db, run_id, owner, state, **fields):
        sources = tuple(s for s in ai_runs.OWNED + (RunState.QUEUED,) if state in ai_runs.TRANSITIONS[s])
        if not ai_runs.transition(db, run_id, state, from_states=sources, owner=owner, finished_at=_now(), lease_owner=None,
                                  lease_expires_at=None, **fields):
            raise LeaseLost(run_id)

    def _settle(self, db, run, owner, failure):
        db.expire(run)
        if run.status in ai_runs.TERMINAL:
            return
        self.stats["failed"] += 1
        state = RunState.CANCELLED if failure.category in ("cancelled", "not_wanted") else RunState.FAILED
        try:
            self._finish(db, run.id, owner, state, error_category=failure.category, error_message=failure.message,
                         http_status=failure.status)
        except LeaseLost:
            return
        self.log("failed", run=run.id, category=failure.category)

    async def _execute(self, db, user, request, run, owner, manager):
        if run.cancel_requested_at or run.status == RunState.CANCEL_REQUESTED:
            self._finish(db, run.id, owner, RunState.CANCELLED, error_category="cancelled", http_status=499,
                         error_message=message_for("cancelled"))
            return None
        req = self.prepare(request.get("prompt") or "", request.get("profile"))  # checked again: the saved request
        attempt = ai_runs.open_attempt(db, run.id)
        if attempt is not None:
            outcome = self._recover(db, user, request, run, owner, attempt, req)
            if outcome:
                return outcome
        if manager and self.is_wanted is not None:
            wanted, why = self.is_wanted(db, run)
            if not wanted:
                self._finish(db, run.id, owner, RunState.CANCELLED, error_category="not_wanted", http_status=409, error_message=why)
                self.log("not_wanted", run=run.id, reason=why)
                return None
        if not request.get("force_regenerate"):
            hit = self.cache.peek(db, user.id, req["identity"], req["hash"])
            if hit:
                self._finish(db, run.id, owner, RunState.COMPLETED, cache_hit=True, asset_id=hit.asset.id, provider="manim",
                             model=req["identity"]["model"], generation_hash=req["hash"])
                return Outcome(asset=hit.asset, cache_hit=True, generated=False, provider="manim", model=req["identity"]["model"],
                               run_id=run.id)
        return await self._render(db, user, request, run, owner, req)

    def _recover(self, db, user, request, run, owner, attempt, req):
        """Continues after an interruption at the last safe checkpoint (never renders what is already rendered)."""
        self.stats["recovered"] += 1
        ai_runs.set_attempt(db, attempt, recovered=(attempt.recovered or 0) + 1, owner=owner)
        self.log("recovering", run=run.id, attempt=attempt.number, state=attempt.state)
        if attempt.asset_id and db.get(models.Asset, attempt.asset_id) is not None:
            ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "finalized_asset"})
            return self._complete(db, user, request, run, owner, attempt, req, db.get(models.Asset, attempt.asset_id))
        if attempt.state == AttemptState.REGISTERING and attempt.output_path and os.path.isfile(attempt.output_path):
            ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "registered_output"})
            asset = self._register(db, user, run, attempt, req, attempt.output_path)
            return self._complete(db, user, request, run, owner, attempt, req, asset)
        # Cut off while rendering (the sandbox died with its server): its workspace is discarded, the render done again
        workspace = json.loads(attempt.detail or "{}").get("workspace_id")
        if workspace:
            self._workspace(workspace).remove()
        ai_runs.set_attempt(db, attempt, state=AttemptState.LOST, error_category="interrupted", finished_at=_now(),
                            error_message="the render was interrupted; it is rendered again", detail_update={"recovered_how": "rendered_again"})
        return None

    def _workspace(self, job_id):
        from manim_sandbox import Workspace
        return Workspace(self.sandbox.root, job_id)

    async def _render(self, db, user, request, run, owner, req):
        lim = req["limits"]
        attempt = ai_runs.new_attempt(db, run.id, owner, "manim", req["identity"]["model"], req["hash"])
        ai_runs.set_attempt(db, attempt, detail_update={"fingerprint": req["fingerprint"], "profile": lim["profile"],
                                                        "workspace_id": attempt.id})
        db.expire(run)
        ai_runs.update_owned(db, run.id, owner, provider="manim", model=req["identity"]["model"], generation_hash=req["hash"],
                             attempts=(run.attempts or 0) + 1, started_at=run.started_at or _now())
        self.log("render_start", run=run.id, attempt=attempt.number, profile=lim["profile"], fingerprint=req["fingerprint"][:12],
                 scope=run.scope_key)
        ws, job = self.sandbox.prepare(attempt.id, run.id, attempt.id, req["code"], req["scene"], lim)
        try:
            ai_runs.set_attempt(db, attempt, state=AttemptState.RENDERING, detail_update={"runtime": self.sandbox.runtime().name})
            self._crash("manim_rendering")
            result = await self._sandboxed(run.id, user.id, ws, job, lim)
            self.stats["renders"] += 1
            ai_runs.set_attempt(db, attempt, detail_update={"metrics": result.metrics, "log": result.log[-2000:] if result.log else None})
            if not result.ok:
                ai_runs.set_attempt(db, attempt, state=AttemptState.CANCELLED if result.category == "cancelled" else AttemptState.FAILED,
                                    error_category=result.category, error_message=result.message, finished_at=_now())
                self.log("render_failed", run=run.id, category=result.category, metrics=result.metrics)
                if result.category == "cancelled":
                    if self.cancel_flags.get(run.id) != "user":
                        raise LeaseLost(run.id)
                    self._finish(db, run.id, owner, RunState.CANCELLED, error_category="cancelled", http_status=499,
                                 error_message=message_for("cancelled"))
                    return None
                raise ManimFailed(result.category, result.message, result.log)
            info = self.validate(result.output, lim)
            dest = os.path.join(self.static_dir, f"manim_{req['fingerprint'][:16]}_{attempt.id[:8]}.mp4")
            shutil.copyfile(result.output, dest + ".part")
            os.replace(dest + ".part", dest)
            ai_runs.set_attempt(db, attempt, state=AttemptState.REGISTERING, output_path=dest,
                                detail_update={"output": {k: info.get(k) for k in ("width", "height", "duration")}})
            self._crash("manim_after_render")
        finally:
            ws.remove()  # source, temp files, logs, partial output: never kept
        asset = self._register(db, user, run, attempt, req, dest)
        return self._complete(db, user, request, run, owner, attempt, req, asset)

    async def _sandboxed(self, run_id, user_id, ws, job, lim):
        """Runs the render in a thread. Whatever stops this worker (the user cancelling, the lease lost to another
        worker, the server stopping) first stops the sandbox's whole process tree and waits for it: a render never
        outlives the worker that owns it."""
        stop = lambda: run_id in self.cancel_flags  # noqa: E731 - "user", "lease" or "stopping"
        future = asyncio.ensure_future(asyncio.to_thread(self._run_in_slot, ws, job, lim, stop, user_id))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.cancel_flags.setdefault(run_id, "stopping")
            while not future.done():
                try:
                    await asyncio.wait([future])
                except asyncio.CancelledError:
                    continue
            if self.cancel_flags.get(run_id) == "user":
                db = SessionLocal()
                try:
                    attempt = ai_runs.open_attempt(db, run_id)
                    if attempt is not None:
                        ai_runs.set_attempt(db, attempt, state=AttemptState.CANCELLED, error_category="cancelled", finished_at=_now())
                finally:
                    db.close()
            raise

    def _run_in_slot(self, ws, job, lim, cancelled, user_id):
        """At most MANIM_MAX_CONCURRENT sandboxes at once on this server and MANIM_MAX_PER_USER for one user; the
        others wait their turn (cancellable) instead of being refused."""
        per_user = max(1, _int(self.env, "MANIM_MAX_PER_USER", 2))
        while True:
            with self._lock:
                if self.rendering.get(user_id, 0) < per_user and self.slots.acquire(blocking=False):
                    self.rendering[user_id] = self.rendering.get(user_id, 0) + 1
                    break
            if cancelled():
                from manim_sandbox import SandboxResult
                return SandboxResult(False, "cancelled", message_for("cancelled"))
            time.sleep(0.25)
        try:
            return self.sandbox.execute(ws, job, lim, cancelled, on_start=self._started)
        finally:
            with self._lock:
                self.rendering[user_id] -= 1
            self.slots.release()

    def _started(self, info):
        self.log("sandbox_started", **info)
        if self.env.get("MANIM_TEST_CRASH_AT") == "manim_during_render":
            time.sleep(1.5)  # the sandbox is rendering: the server dies, the Job Object takes the sandbox with it
            self._crash("manim_during_render")

    def validate(self, path, lim):
        """The rendered video, checked like any media before it becomes an asset: decodable, a video, the profile's
        size and frame rate, within the duration and size limits."""
        try:
            info = inspect_media(path)
        except MediaError as e:
            raise ManimFailed("invalid_output", detail=str(e))
        if info["kind"] != "video" or not info.get("verified"):
            raise ManimFailed("invalid_output", detail="not a verified video")
        if (info.get("width"), info.get("height")) != (lim["width"], lim["height"]):
            raise ManimFailed("resolution_limit", detail=f"{info.get('width')}x{info.get('height')}")
        if not info.get("duration") or info["duration"] > lim["max_duration"] + 0.5:
            raise ManimFailed("frame_limit" if info.get("duration") else "invalid_output", detail=f"{info.get('duration')} s")
        if os.path.getsize(path) > lim["output_mb"] * 1048576:
            raise ManimFailed("output_limit")
        return info

    def _register(self, db, user, run, attempt, req, path):
        """The render as a library asset: adopted in place (the same file is the same asset, so a repeat after a crash
        finds it)."""
        name = os.path.basename(path)
        metrics = json.loads(attempt.detail or "{}").get("metrics") or {}
        details = {"scene": req["scene"], "generation": {
            "hash": req["hash"], "provider": "manim", "model": req["identity"]["model"], "media_type": "video",
            "parameters": req["identity"]["parameters"], "fingerprint": req["fingerprint"], "profile": req["limits"]["profile"],
            "run_id": run.id, "attempt_id": attempt.id, "runtime": json.loads(attempt.detail or "{}").get("runtime"),
            "render_seconds": metrics.get("seconds"), "cpu_seconds": metrics.get("cpu_seconds"),
            "peak_memory_mb": metrics.get("peak_memory_mb"), "frames": metrics.get("frames")}}
        try:
            asset = self.library.adopt(db, "static", name, owner_id=user.id, source="manim", file_name=name, details=details)
        except MediaError as e:
            raise ManimFailed("invalid_output", detail=str(e))
        ai_runs.set_attempt(db, attempt, asset_id=asset.id)
        self._crash("manim_after_register")
        return asset

    def _complete(self, db, user, request, run, owner, attempt, req, asset):
        self.cache.record_once(db, f"user:{user.id}", asset, req["identity"], req["hash"], forced=bool(request.get("force_regenerate")))
        ai_runs.set_attempt(db, attempt, state=AttemptState.COMPLETED, finished_at=_now())
        self._finish(db, run.id, owner, RunState.COMPLETED, asset_id=asset.id, provider="manim", model=req["identity"]["model"],
                     generation_hash=req["hash"], error_category=None, error_message=None, http_status=None)
        self.log("completed", run=run.id, asset=asset.id[:8], fingerprint=req["fingerprint"][:12])
        if self.on_completed is not None:
            try:
                db.expire(run)
                self.on_completed(db, run)
            except Exception as e:  # noqa: BLE001 - the render is saved; the lesson update is an extra
                db.rollback()
                self.log("lesson_update_failed", run=run.id, error=str(e))
        return Outcome(asset=asset, cache_hit=False, generated=True, provider="manim", model=req["identity"]["model"], run_id=run.id)

    # ---- cancel, sweep ------------------------------------------------------------------------------------------

    def request_cancel(self, db, run):
        if run.status == RunState.QUEUED and ai_runs.transition(
                db, run.id, RunState.CANCELLED, from_states=(RunState.QUEUED,), finished_at=_now(), lease_owner=None,
                lease_expires_at=None, error_category="cancelled", http_status=499, error_message="Cancelled before it started."):
            return "cancelled"
        if not ai_runs.transition(db, run.id, RunState.CANCEL_REQUESTED, from_states=(RunState.RUNNING, RunState.RECOVERING),
                                  cancel_requested_at=_now()):
            return None
        if run.id in self.tasks:
            self.cancel_flags[run.id] = "user"  # the sandbox's watcher stops the render (its whole process tree)
        return "cancelling"

    def sweep(self, db):
        """Workspaces left by a crash: removed when their attempt is no longer being worked on."""
        t = _now()

        def active(attempt_id):
            if not attempt_id:
                return False
            row = db.get(models.AIGenerationAttempt, attempt_id)
            if row is None or row.state not in ai_runs.ATTEMPT_OPEN:
                return False
            run = db.get(models.AIGenerationRun, row.run_id)
            return run is not None and run.lease_owner is not None and bool(run.lease_expires_at) and run.lease_expires_at > t
        removed = self.sandbox.sweep(active, older_than=max(60, self.cfg()["lease"] * 2))
        if removed:
            self.log("workspaces_swept", removed=removed)
        return removed
