"""AI media layer (Phase 8; durable and recoverable since Phase 9): how Aadhi obtains an AI image or video,
whichever provider makes it, without losing or duplicating work when something is interrupted.

  MediaRequest ─► provider choice (ai_providers.ProviderRegistry.select: preference, capabilities,
  configuration, recent failures, fallback order)
    ─► AI media cache (Phase 5, ai_cache.py): the preferred provider's earlier result for the same
       request; a fallback provider's earlier result only when that fallback would be used
    ─► a durable run (models.AIGenerationRun: the request itself, its identity, the worker's lease);
       an identical request while it is active attaches to it instead of starting another
    ─► attempts (models.AIGenerationAttempt), one per provider call: the provider's job id and handle
       are saved as soon as the provider accepted it; bounded retries for transient failures (resuming
       the same provider job, never submitting a second one), then the next provider when allowed
    ─► validate the media ─► library asset (Phase 3, idempotent) ─► cache entry under the identity of
       the provider that really made it (idempotent) ─► run completed (guarded: only the lease owner)

Interrupted runs (the worker's lease expired: server restart, crash) are picked up by
ai_recovery.RecoveryManager and continued by `execute`: a submitted provider job is polled again, a
finished one fetched, a downloaded file registered, a registered asset finalized — never generated again.
When continuing could duplicate a paid generation (the provider may have accepted a request that cannot
be found again), the run stops as needs_attention and a person decides.

The generator endpoints (server.py) and the visual router's cache step (visuals.py) use this module;
neither knows a provider's details. `call_provider` / `resume_provider` are the only places a provider
is asked for media.
"""
import asyncio
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

import ai_runs
import models
from ai_cache import generation_hash, generation_identity, normalize_text, provenance
from ai_providers import (COMPLETED, IMAGE, PRESENTER, RUNNING, VIDEO, media_kind, Failure, GenerationResult, MediaRequest, ProviderError,
                          _env_flag, _env_float, error_from_exception, max_bytes_for, ratio_of, sanitize)
from ai_runs import AttemptState, LeaseLost, RunState
from database import SessionLocal
from media import MediaError, inspect_media

MIN_BYTES = 256            # smaller than any real picture or clip
MIN_SIDE = 32              # pixels
MIN_VIDEO_SECONDS = 0.5
RUN_STALE_SECONDS = 90     # with recovery switched off: a run whose heartbeat stopped is reported interrupted
ATTACH_WAIT_SECONDS = 1800  # a waiting request attached to an identical run waits at most this long


class GenerationFailed(Exception):
    """No media could be obtained: `status` is the HTTP status the API answers, `message` is safe to show."""

    def __init__(self, status, message, *, category=None, errors=(), retry_after=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.category = category
        self.errors = list(errors)
        self.retry_after = retry_after


class NeedsAttention(GenerationFailed):
    """Continuing automatically could start a duplicate (possibly paid) generation: a person decides."""

    def __init__(self, message, category):
        super().__init__(409, message, category=category)


@dataclass
class Outcome:
    asset: object = None
    cache_hit: bool = False
    generated: bool = False
    provider: str | None = None
    model: str | None = None
    requested_provider: str | None = None
    fallback_from: str | None = None
    run_id: str | None = None
    warnings: list = field(default_factory=list)
    generation_ms: int | None = None
    manual: dict | None = None   # the manual workflow's answer (status manual_required, filename, prompt)

    def info(self):
        """The provenance fields the API adds to a generator answer."""
        data = {"provider": self.provider, "model": self.model}
        for key in ("requested_provider", "fallback_from", "run_id", "generation_ms"):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        if self.warnings:
            data["warnings"] = self.warnings
        return data


def _now():
    return datetime.datetime.utcnow()


def _run_request_json(request):
    """The run's stored request (ai_runs.request_json), plus the scene's id when the request names one (Phase 20: the
    result is attached to that scene even after it moved). Requests without a scene id are stored exactly as before."""
    text = ai_runs.request_json(request)
    scene_id = getattr(request, "scene_id", None)
    if isinstance(scene_id, str) and scene_id:
        data = json.loads(text)
        data["scene_id"] = scene_id
        text = json.dumps(data, sort_keys=True)
    return text


def looks_blank(path, media_type):
    """True when every sampled frame is one flat colour (a typical failed generation), None if unknown."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    w, h = 32, 18
    vf = f"fps=1,scale={w}:{h},format=gray" if media_type == VIDEO else f"scale={w}:{h},format=gray"
    try:
        out = subprocess.run([exe, "-v", "error", "-i", path, "-vf", vf, "-frames:v", "8" if media_type == VIDEO else "1",
                              "-f", "rawvideo", "-"], capture_output=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    size = w * h
    frames = [out[i:i + size] for i in range(0, len(out) - size + 1, size)]
    if not frames:
        return None
    return all(max(frame) - min(frame) <= 8 for frame in frames)


class _Progress:
    """What a provider adapter reports while it works (ai_providers.JobProvider): the job it started (saved
    at once), each status check, the start of the download."""

    def __init__(self, submitted=None, checked=None, downloading=None):
        self._submitted, self._checked, self._downloading = submitted, checked, downloading

    def __call__(self, status, job_id):
        pass

    def submitted(self, job_id, handle):
        if self._submitted:
            self._submitted(job_id, handle)

    def checked(self):
        if self._checked:
            self._checked()

    def downloading(self):
        if self._downloading:
            self._downloading()


class AIMediaService:
    def __init__(self, library, cache, registry, *, static_dir, generation_enabled, env=None, log=None):
        self.library = library
        self.cache = cache
        self.registry = registry
        self.static_dir = static_dir
        self.generation_enabled = generation_enabled
        self.env = os.environ if env is None else env
        self.log_enabled = _env_flag(self.env, "AI_MEDIA_LOG", True) if log is None else log
        self.instance = uuid.uuid4().hex[:12]
        self.stats = {"attempts": 0, "retries": 0, "fallbacks": 0, "provider_failures": 0, "invalid_outputs": 0,
                      "recovered": 0, "resumed_jobs": 0, "attached": 0, "needs_attention": 0}
        self.provider_calls = {VIDEO: 0, IMAGE: 0, PRESENTER: 0}
        self.calls_by_provider = {}
        self.tasks = {}          # run id -> asyncio task running it in this process
        self.active = set()      # run ids this process is working on
        self.cancel_flags = {}   # run id -> "user" | "lease" (why the task was cancelled)
        self.on_completed = None  # callback(db, run): the lesson that asked for it is updated (server.py)
        self.is_wanted = None     # callback(db, run) -> (bool, reason): Visual Review still wants this visual
        self.wake = None          # callback(): tells the recovery manager that queued work exists

    # ---- observability -----------------------------------------------------------------------------

    def log(self, event, **fields):
        if not self.log_enabled:
            return
        clean = {k: (sanitize(v) if isinstance(v, str) else v) for k, v in fields.items() if v is not None}
        print("[AI MEDIA] " + json.dumps({"event": event, **clean}, sort_keys=True, default=str))

    def snapshot(self):
        return {"media": dict(self.stats), "provider_calls": dict(self.provider_calls),
                "provider_calls_by_provider": dict(self.calls_by_provider)}

    def cfg(self):
        return ai_runs.settings(self.env)

    def _crash(self, point):
        """Test-only crash points (AI_TEST_CRASH_AT, stand-in providers only): the process dies right here,
        without cleanup, like a power cut — for the restart/recovery checks."""
        if self.registry.fake and self.env.get("AI_TEST_CRASH_AT") == point:
            print(f"[AI MEDIA] simulated crash at {point}", flush=True)
            os._exit(97)

    # ---- identities and planning ----------------------------------------------------------------------

    @staticmethod
    def identity(candidate, request):
        settings = candidate.provider.settings_for(request)
        identity = generation_identity(request.media_type, candidate.name, request.prompt, settings)
        return identity, generation_hash(identity), settings

    def plan(self, media_type, prompt, generation_possible):
        """For the visual router: the cache identities that count as this request, in order, and the provider
        that would generate it on a miss. Never generates. Returns (lookups [(Candidate, identity)], choice, selection)."""
        request = MediaRequest(media_type=media_type, prompt=normalize_text(prompt))
        selection = self.registry.select(request)
        usable = selection.usable
        choice = usable[0] if usable else None
        lookups = []
        for candidate in selection.candidates:
            if not candidate.supported:
                continue
            lookups.append((candidate, self.identity(candidate, request)[0]))
            if generation_possible and choice is not None and candidate.name == choice.name:
                break  # the preferred provider's results first; a fallback's only if the fallback would be used
        return lookups, choice, selection

    @staticmethod
    def request_hash(user_id, request):
        """The logical request: who, what (normalized), how, and where it is for. Identical requests while one
        is active attach to it; a forced request is its own request."""
        data = {"scope": f"user:{user_id}", "media": request.media_type, "prompt": normalize_text(request.prompt),
                "provider": request.provider, "fallback": request.allow_fallback, "model": request.model,
                "aspect": request.aspect_ratio, "duration": request.duration_seconds, "force": bool(request.force_regenerate),
                "project": request.project_id, "scene": request.scene_index, "slot": request.slot,
                "presenter": request.presenter and {k: v for k, v in request.presenter.items() if k != "speech"},
                "speech": request.presenter and (request.presenter.get("speech") or {}).get("audio_sha256")}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()

    # ---- entry points -------------------------------------------------------------------------------------

    async def generate(self, db, user, request):
        """An AI image or video for the request, waiting for it: cached if it was made before, otherwise
        generated (or the identical run already active is waited for). Returns an Outcome or raises GenerationFailed."""
        outcome, plan = self.resolve(db, user, request)
        if outcome:
            return outcome
        owner = ai_runs.worker_token(self.instance)
        run, attached = self.open_run(db, user, request, owner)
        if attached:
            self.cache.count("waited", run.generation_hash)  # waits for the identical request (Phase 5 statistic)
            return await self.wait_for(db, run.id)
        return await self.execute(run.id, owner, plan=plan, db=db)

    def start_background(self, db, user, request):
        """Answers at once what needs no provider (cache hit, AI off, manual workflow: (Outcome, None)); otherwise
        the generation continues on the server and (None, run) is returned to follow (an identical active run
        is returned instead of starting another)."""
        outcome, plan = self.resolve(db, user, request)
        if outcome:
            return outcome, None
        owner = ai_runs.worker_token(self.instance)
        run, attached = self.open_run(db, user, request, owner, background=True)
        if not attached:
            self.spawn(run.id, owner, plan=plan)
        return None, run

    def spawn(self, run_id, owner, plan=None, manager=False):
        task = asyncio.get_running_loop().create_task(self.execute(run_id, owner, plan=plan, manager=manager, quiet=True))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self.tasks.pop(rid, None))
        return task

    def resolve(self, db, user, request, run=None, owner=None):
        """Everything that needs no provider call: the choice of providers, the cache, the AI switch, the
        manual workflow. Returns (Outcome, None) when that already answers the request, else (None, plan)
        for a provider. Raises GenerationFailed."""
        request.prompt = normalize_text(request.prompt)
        if not request.prompt:
            raise GenerationFailed(400, "A prompt is required.", category=Failure.REJECTED)
        selection = self.registry.select(request)
        candidates = [c for c in selection.candidates if c.supported]
        preferred = selection.candidates[0].name if selection.candidates else None
        identities = {c.name: self.identity(c, request) for c in candidates}
        plan = {"selection": selection, "candidates": candidates, "preferred": preferred, "identities": identities,
                "checked": set()}
        self.log("select", media_type=request.media_type, explicit=selection.explicit, fallback=selection.fallback,
                 candidates=[f"{c.name}:{'usable' if c.usable else (c.reason or 'no')}" for c in selection.candidates])
        if request.provider and (not selection.candidates or not selection.candidates[0].supported) and not selection.fallback:
            raise self._unavailable(selection, request)
        usable = selection.usable
        first = usable[0].name if usable else None
        # 1. Made before? The preferred provider's result, or a fallback's when the fallback would be tried first
        if not request.force_regenerate:
            for candidate in candidates:
                hit = self._lookup(db, user, plan, candidate)
                if hit:
                    return self._hit(db, run, owner, hit, candidate, preferred, request), None
                if candidate.name == first:
                    break
        # 2. AI generation off: any earlier result of the candidates is still usable, nothing new is made
        if not self.generation_enabled():
            if not request.force_regenerate:
                for candidate in candidates:
                    if candidate.name not in plan["checked"]:
                        hit = self._lookup(db, user, plan, candidate)
                        if hit:
                            return self._hit(db, run, owner, hit, candidate, preferred, request), None
            raise GenerationFailed(403, f"AI {request.media_type} generation is turned off on this server.", category="disabled")
        if not usable:
            raise self._unavailable(selection, request)
        if usable[0].name == "manual":
            return self._manual(db, user, request, identities["manual"], run, owner), None
        plan["usable"] = usable
        return None, plan

    def _lookup(self, db, user, plan, candidate):
        plan["checked"].add(candidate.name)
        identity, digest, _ = plan["identities"][candidate.name]
        return self.cache.lookup(db, user.id, identity, digest)

    # ---- runs --------------------------------------------------------------------------------------------------

    def open_run(self, db, user, request, owner, background=False, batch_id=None, leased=True):
        """The run for this request: the identical active one (attached), or a new one — owned by `owner`
        (leased: it starts at once) or queued for the recovery manager's workers."""
        request.prompt = normalize_text(request.prompt)
        digest = self.request_hash(user.id, request)
        existing = (db.query(models.AIGenerationRun)
                    .filter(models.AIGenerationRun.scope_key == f"user:{user.id}", models.AIGenerationRun.request_hash == digest,
                            models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE)))
                    .order_by(models.AIGenerationRun.created_at.desc()).first())
        if existing:
            self.stats["attached"] += 1
            self.log("attached", run=existing.id, media_type=request.media_type)
            return existing, True
        t = _now()
        lease = self.cfg()["lease"]
        run = models.AIGenerationRun(
            id=uuid.uuid4().hex, scope_key=f"user:{user.id}", user_id=user.id, media_type=request.media_type,
            status=RunState.RUNNING if leased else RunState.QUEUED,
            requested_provider=request.provider or self.registry.preferred(request.media_type),
            forced=bool(request.force_regenerate), instance=self.instance, created_at=t, heartbeat_at=t,
            request=_run_request_json(request), request_hash=digest, project_id=request.project_id,
            scene_index=request.scene_index, slot=request.slot, batch_id=batch_id, recovery_count=0, allow_duplicate=False,
            lease_owner=owner if leased else None, lease_expires_at=t + datetime.timedelta(seconds=lease) if leased else None,
            detail=json.dumps({"background": background, "purpose": request.purpose, "project_id": request.project_id,
                               "scene_index": request.scene_index, "slot": request.slot, "attempts": []}))
        db.add(run)
        db.commit()
        return run, False

    async def wait_for(self, db, run_id, timeout=ATTACH_WAIT_SECONDS):
        """A waiting request attached to an identical active run: its result, once it has one."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            db.expire_all()
            run = db.get(models.AIGenerationRun, run_id)
            if run.status in ai_runs.TERMINAL:
                return self.outcome_of(db, run, attached=True)
            await asyncio.sleep(0.25)
        raise GenerationFailed(504, "The generation is still running; follow it in the AI providers panel.", category=Failure.TIMEOUT)

    def outcome_of(self, db, run, attached=False):
        """The result of a finished run; for a request that attached to it the media is reused (cache_hit),
        as it was made by the other request."""
        if run.status == RunState.COMPLETED and run.asset_id:
            asset = db.get(models.Asset, run.asset_id)
            if asset is not None:
                detail = json.loads(run.detail or "{}")
                reused = attached or bool(run.cache_hit)
                return Outcome(asset=asset, cache_hit=reused, generated=not reused, provider=run.provider,
                               model=run.model, requested_provider=run.requested_provider, fallback_from=run.fallback_from,
                               run_id=run.id, warnings=detail.get("warnings") or [])
        category = run.error_category or ("cancelled" if run.status == RunState.CANCELLED else None)
        status = run.http_status or {RunState.CANCELLED: 499, RunState.NEEDS_ATTENTION: 409}.get(run.status, 502)
        raise GenerationFailed(status, run.error_message or "The generation did not finish.", category=category)

    def _detail(self, db, run, owner, **updates):
        """Adds to the run's JSON detail (the Phase 8 per-try list is kept)."""
        db.expire(run)
        data = json.loads(run.detail or "{}")
        for key, value in updates.items():
            if key == "attempt":
                data.setdefault("attempts", []).append(value)
            else:
                data[key] = value
        ai_runs.update_owned(db, run.id, owner, detail=json.dumps(data))

    def _finish(self, db, run, owner, state, **fields):
        """The run's last state change, only by its owner; the lease is given up with it."""
        sources = tuple(s for s in ai_runs.OWNED + (RunState.QUEUED,) if state in ai_runs.TRANSITIONS[s])
        done = ai_runs.transition(db, run.id, state, from_states=sources, owner=owner,
                                  finished_at=_now(), lease_owner=None, lease_expires_at=None, **fields)
        if not done:
            raise LeaseLost(run.id)
        db.expire(run)

    # ---- execution (normal and recovered) ------------------------------------------------------------------------

    async def execute(self, run_id, owner, plan=None, manager=False, quiet=False, db=None):
        """Works on a run this worker owns until it is completed, failed, cancelled or needs attention.
        `manager`: started by the recovery manager (transient provider trouble defers the run instead of
        failing it). `quiet`: nobody waits for the result (background)."""
        own_db = db is None
        db = db or SessionLocal()
        me = asyncio.current_task()
        self.tasks.setdefault(run_id, me)
        self.active.add(run_id)
        heartbeat = asyncio.ensure_future(self._heartbeat(run_id, owner, me))
        try:
            run = db.get(models.AIGenerationRun, run_id)
            user = db.get(models.User, run.user_id)
            request = ai_runs.request_from(run)
            try:
                outcome = await self._execute(db, user, request, run, owner, plan, manager)
            except GenerationFailed as failure:
                self._settle_failure(db, run, owner, failure, manager)
                if quiet:
                    return None
                raise
            return outcome
        except LeaseLost:
            self.log("lease_lost", run=run_id)
            if quiet:
                return None
            raise GenerationFailed(503, "Another worker took over this generation.", category="interrupted")
        except asyncio.CancelledError:
            why = self.cancel_flags.pop(run_id, None)
            db.rollback()
            if why == "user":
                await self._cancel_now(db, run_id, owner)
            elif why != "lease":
                self._expire_lease(db, run_id, owner)  # server stopping: recovery may take it over at once
            if not quiet:
                raise
            return None
        finally:
            heartbeat.cancel()
            self.active.discard(run_id)
            if self.tasks.get(run_id) is me:
                self.tasks.pop(run_id, None)
            if own_db:
                db.close()

    async def _execute(self, db, user, request, run, owner, plan, manager):
        if run.cancel_requested_at or run.status == RunState.CANCEL_REQUESTED:
            await self._cancel_now(db, run.id, owner)  # the user cancelled before this worker took over
            return None
        skip = set()
        # A recovered run: continue the provider work that was under way (never submit it twice)
        attempt = ai_runs.open_attempt(db, run.id)
        if attempt is not None:
            self.stats["recovered"] += 1
            outcome, skip = await self._recover(db, user, request, run, owner, attempt, manager)
            if outcome:
                return outcome
        elif ai_runs.attempts_of(db, run.id):
            skip = {a.provider for a in ai_runs.attempts_of(db, run.id)
                    if a.state in (AttemptState.FAILED,) and a.error_category not in (Failure.RATE_LIMITED, Failure.UNAVAILABLE, Failure.TIMEOUT)}
        # Work continued without the user (recovery, batches): is the visual still wanted (Visual Review)?
        if manager and self.is_wanted is not None:
            wanted, why = self.is_wanted(db, run)
            if not wanted:
                self._finish(db, run, owner, RunState.CANCELLED, error_category="not_wanted", http_status=409, error_message=why)
                self.log("not_wanted", run=run.id, reason=why)
                raise GenerationFailed(409, why, category="not_wanted")
        if plan is None:
            outcome, plan = self.resolve(db, user, request, run=run, owner=owner)
            if outcome:
                return outcome
        return await self._produce(db, user, request, run, owner, plan, skip)

    def _settle_failure(self, db, run, owner, failure, manager):
        """The run's end after a failure: deferred (recovery backoff), needs attention, or failed."""
        db.expire(run)
        if run.status in ai_runs.TERMINAL:
            return
        transient = failure.category in (Failure.RATE_LIMITED, Failure.UNAVAILABLE, Failure.TIMEOUT)
        if manager and transient and (run.recovery_count or 0) < self.cfg()["max_recoveries"]:
            wait = min(900, 30 * 2 ** (run.recovery_count or 0))
            if ai_runs.transition(db, run.id, RunState.QUEUED, from_states=(RunState.RUNNING, RunState.RECOVERING), owner=owner,
                                  lease_owner=None, lease_expires_at=None, not_before=_now() + datetime.timedelta(seconds=wait),
                                  recovery_count=(run.recovery_count or 0) + 1, error_category=failure.category,
                                  error_message=failure.message):
                self.log("deferred", run=run.id, category=failure.category, wait=wait)
                return
        state = RunState.NEEDS_ATTENTION if isinstance(failure, NeedsAttention) else \
            (RunState.CANCELLED if failure.category == Failure.CANCELLED else RunState.FAILED)
        if state == RunState.NEEDS_ATTENTION:
            self.stats["needs_attention"] += 1
        try:
            self._finish(db, run, owner, state, error_category=failure.category, error_message=failure.message,
                         http_status=failure.status)
        except LeaseLost:
            return
        self.log("failed" if state != RunState.NEEDS_ATTENTION else "needs_attention", run=run.id, status=failure.status,
                 category=failure.category, media_type=run.media_type)

    async def _produce(self, db, user, request, run, owner, plan, skip=()):
        """Generates with the usable providers in order (those already failed in this run skipped): each with
        bounded retries, the next one only when the failure allows it (and fallback is allowed)."""
        selection, preferred, identities = plan["selection"], plan["preferred"], plan["identities"]
        usable = [c for c in plan["usable"] if c.name not in skip]
        if not usable:
            raise GenerationFailed(502, "No provider is left to try for this generation.", category=Failure.UNAVAILABLE)
        ai_runs.update_owned(db, run.id, owner, started_at=run.started_at or _now())
        self._detail(db, run, owner, selection=selection.explain())
        errors = []
        scope = f"user:{user.id}"
        for n, candidate in enumerate(usable):
            identity, digest, settings = identities[candidate.name]
            if n > 0:
                self.stats["fallbacks"] += 1
                self.log("fallback", media_type=request.media_type, from_provider=usable[n - 1].name, to_provider=candidate.name, run=run.id)
                if not request.force_regenerate and candidate.name not in plan["checked"]:
                    hit = self._lookup(db, user, plan, candidate)  # the fallback made it before: reused, not made twice
                    if hit:
                        return self._hit(db, run, owner, hit, candidate, preferred, request)
            lock = None
            if request.force_regenerate:
                if n == 0:
                    self.cache.count("bypassed", digest)
            else:
                hit, lock = await self.cache.acquire(db, scope, digest, lambda i=identity, d=digest: self.cache.peek(db, user.id, i, d))
                if hit:  # an identical request finished it meanwhile
                    return self._hit(db, run, owner, hit, candidate, preferred, request)
            try:
                return await self._attempt(db, user, request, run, owner, candidate.provider, identity, digest, settings,
                                           fallback_from=preferred if candidate.name != preferred else None, requested=preferred)
            except ProviderError as error:
                errors.append(error)
                self.stats["provider_failures"] += 1
                self.registry.note_failure(candidate.name, error)
                self.log("provider_failed", media_type=request.media_type, provider=candidate.name, category=error.category,
                         message=error.message, run=run.id)
                if not (error.fallback_ok and selection.fallback):
                    break
            finally:
                self.cache.release(db, lock)
        raise self._failed(errors, request)

    def _output_path(self, media_type, digest, attempt, owner):
        tag = f"{attempt.id[:8]}{owner[-4:]}"
        if media_type == PRESENTER:  # a new file per generation, like videos: an earlier version stays as lessons know it
            return os.path.join(self.static_dir, f"ai_presenter_{digest[:16]}_{tag}.mp4")
        if media_type == VIDEO:
            # A new file for every generation: an earlier version stays exactly as lessons know it
            return os.path.join(self.static_dir, f"ai_video_{digest[:16]}_{tag}.mp4")
        # Private work folder beside the library's store (never public; survives a restart for recovery)
        work = os.path.join(self.library.volumes[self.library.managed].root, "ai-work") \
            if getattr(self.library, "volumes", None) else os.path.join(os.path.dirname(os.path.abspath(self.static_dir)), "ai-work")
        os.makedirs(work, exist_ok=True)
        return os.path.join(work, f"ai_image_{digest[:12]}_{tag}.jpg")  # the library reads the real type from the bytes

    def _progress(self, db, run, owner, attempt):
        def submitted(job_id, handle):
            self._crash("before_job_saved")
            ai_runs.set_attempt(db, attempt, state=AttemptState.SUBMITTED, provider_job_id=job_id,
                                provider_job=json.dumps(handle), submitted_at=_now())
            ai_runs.update_owned(db, run.id, owner, provider_job_id=job_id)
            self.log("job_saved", run=run.id, attempt=attempt.number, provider=attempt.provider, job=job_id)
            self._crash("after_job_saved")

        def checked():
            t = _now()
            if not attempt.last_checked_at or (t - attempt.last_checked_at).total_seconds() >= 5:
                ai_runs.set_attempt(db, attempt, last_checked_at=t)
                ai_runs.update_owned(db, run.id, owner, last_checked_at=t)

        def downloading():
            ai_runs.set_attempt(db, attempt, state=AttemptState.DOWNLOADING)

        return _Progress(submitted, checked, downloading)

    async def call_provider(self, adapter, request, settings, dest, progress=None):
        """The only place a provider is asked for new media."""
        self.provider_calls[request.media_type] = self.provider_calls.get(request.media_type, 0) + 1
        self.calls_by_provider[adapter.name] = self.calls_by_provider.get(adapter.name, 0) + 1
        self.stats["attempts"] += 1
        return await adapter.generate(request, settings, dest, progress)

    async def resume_provider(self, adapter, handle, request, settings, dest, progress=None):
        """Continues a provider job started earlier (never a new submission)."""
        self.stats["resumed_jobs"] += 1
        return await adapter.resume(handle, request, settings, dest, progress)

    async def _attempt(self, db, user, request, run, owner, adapter, identity, digest, settings, *, fallback_from, requested):
        max_tries = max(1, int(_env_float(self.env, "AI_PROVIDER_MAX_ATTEMPTS", 2)))
        max_wait = _env_float(self.env, "AI_PROVIDER_MAX_RETRY_WAIT", 20)
        delay = _env_float(self.env, "AI_PROVIDER_RETRY_DELAY", 1.0)
        started = time.time()
        for number in range(1, max_tries + 1):
            attempt = ai_runs.new_attempt(db, run.id, owner, adapter.name, settings.get("model"), digest)
            ai_runs.set_attempt(db, attempt, detail_update={"identity": identity, "fallback_from": fallback_from,
                                                            "requested": requested})
            db.expire(run)
            ai_runs.update_owned(db, run.id, owner, provider=adapter.name, model=settings.get("model"), generation_hash=digest,
                                 attempts=(run.attempts or 0) + 1, fallback_from=fallback_from)
            self.log("generate", media_type=request.media_type, provider=adapter.name, model=settings.get("model"),
                     hash=digest[:12], attempt=attempt.number, forced=request.force_regenerate, run=run.id)
            dest = self._output_path(request.media_type, digest, attempt, owner)
            t0 = time.time()
            try:
                await self.registry.slot(adapter.name)
                try:
                    result = await self.call_provider(adapter, request, settings, dest, self._progress(db, run, owner, attempt))
                finally:
                    self.registry.release(adapter.name)
            except Exception as exc:  # noqa: BLE001 - classified; CancelledError passes through
                error = error_from_exception(exc, adapter.name)
                self._remove(dest)
                ai_runs.set_attempt(db, attempt, state=AttemptState.FAILED, error_category=error.category,
                                    error_message=error.message, finished_at=_now())
                self._detail(db, run, owner, attempt={"provider": adapter.name, "attempt": number, "category": error.category,
                                                      "message": error.message, "ms": int((time.time() - t0) * 1000)})
                wait = error.retry_after if error.retry_after is not None else delay * (2 ** (number - 1))
                if error.retryable and number < max_tries and wait <= max_wait:
                    if attempt.provider_job_id and getattr(adapter, "recoverable", False):
                        # The provider accepted a job: a hiccup while following it never submits a second one
                        try:
                            return await self._resume_attempt(db, user, request, run, owner, adapter, attempt, identity, digest,
                                                              settings, fallback_from, requested, started)
                        except ProviderError as again:
                            error = again
                            raise error
                    self.stats["retries"] += 1
                    self.log("retry", provider=adapter.name, category=error.category, wait=round(wait, 2), attempt=number, run=run.id)
                    await asyncio.sleep(wait)
                    continue
                raise error
            self._detail(db, run, owner, attempt={"provider": adapter.name, "attempt": number, "category": None,
                                                  "ms": int((time.time() - t0) * 1000)})
            return self._finalize(db, user, request, run, owner, adapter, attempt, identity, digest, settings, dest, result,
                                  fallback_from=fallback_from, requested=requested, started=started)

    async def _resume_attempt(self, db, user, request, run, owner, adapter, attempt, identity, digest, settings,
                              fallback_from, requested, started):
        handle = json.loads(attempt.provider_job or "{}")
        ai_runs.set_attempt(db, attempt, state=AttemptState.SUBMITTED, error_category=None, error_message=None, finished_at=None,
                            owner=owner)
        dest = self._output_path(request.media_type, digest, attempt, owner)
        try:
            await self.registry.slot(adapter.name)
            try:
                result = await self.resume_provider(adapter, handle, request, settings, dest, self._progress(db, run, owner, attempt))
            finally:
                self.registry.release(adapter.name)
        except Exception as exc:  # noqa: BLE001
            error = error_from_exception(exc, adapter.name)
            self._remove(dest)
            ai_runs.set_attempt(db, attempt, state=AttemptState.LOST if error.category == Failure.LOST else AttemptState.FAILED,
                                error_category=error.category, error_message=error.message, finished_at=_now())
            raise error
        return self._finalize(db, user, request, run, owner, adapter, attempt, identity, digest, settings, dest, result,
                              fallback_from=fallback_from, requested=requested, started=started)

    def _finalize(self, db, user, request, run, owner, adapter, attempt, identity, digest, settings, path, result, *,
                  fallback_from, requested, started, asset=None):
        """From provider output to a completed run: validate, library asset, cache entry, run completed.
        Idempotent at every step: called again after a crash it finds what was already done."""
        metadata = result.metadata if isinstance(result, GenerationResult) else (result if isinstance(result, dict) else {})
        job_id = (result.provider_job_id if isinstance(result, GenerationResult) else None) or attempt.provider_job_id
        warnings = json.loads(attempt.detail or "{}").get("warnings") or []
        if asset is None:
            ai_runs.set_attempt(db, attempt, state=AttemptState.REGISTERING, output_path=path)
            self._crash("after_download")
            try:
                info, warnings = self.validate(path, request, settings, adapter)
            except ProviderError as error:
                self._remove(path)
                ai_runs.set_attempt(db, attempt, state=AttemptState.FAILED, error_category=error.category,
                                    error_message=error.message, finished_at=_now())
                raise
            elapsed = int((time.time() - started) * 1000)
            db.expire(run)
            asset = self._register(db, user, request, path, info, identity, digest, provenance_extra={
                "seed": metadata.get("seed"), "requested_provider": requested if requested != adapter.name else None,
                "fallback_from": fallback_from, "attempts": run.attempts, "generation_ms": elapsed, "job_id": job_id,
                "run_id": run.id, "warnings": warnings or None, "purpose": request.purpose})
            ai_runs.set_attempt(db, attempt, asset_id=asset.id, detail_update={"warnings": warnings})
            self._crash("after_register")
        self.cache.record_once(db, f"user:{user.id}", asset, identity, digest, forced=request.force_regenerate)
        self._crash("after_cache")
        self.cache.count("generated", digest, f"asset {asset.id[:8]}")
        self.registry.note_success(adapter.name)
        ai_runs.set_attempt(db, attempt, state=AttemptState.COMPLETED, finished_at=_now())
        elapsed = int((time.time() - started) * 1000)
        self._detail(db, run, owner, warnings=warnings)
        self._finish(db, run, owner, RunState.COMPLETED, asset_id=asset.id, provider=adapter.name, model=settings.get("model"),
                     provider_job_id=job_id, generation_hash=digest, fallback_from=fallback_from, error_category=None,
                     error_message=None, http_status=None)
        if request.media_type == IMAGE:
            self._remove(path or attempt.output_path)  # the library holds its own copy
        self.log("completed", media_type=request.media_type, provider=adapter.name, hash=digest[:12], ms=elapsed,
                 asset=asset.id[:8], fallback_from=fallback_from, warnings=len(warnings) or None, run=run.id)
        self._completed(db, run)
        return Outcome(asset=asset, cache_hit=False, generated=True, provider=adapter.name, model=settings.get("model"),
                       requested_provider=requested, fallback_from=fallback_from, run_id=run.id, warnings=warnings,
                       generation_ms=elapsed)

    def _completed(self, db, run):
        if self.on_completed is None:
            return
        try:
            db.expire(run)
            self.on_completed(db, run)
        except Exception as e:  # noqa: BLE001 - the media is made; updating the lesson is an extra
            db.rollback()
            self.log("lesson_update_failed", run=run.id, error=str(e))

    @staticmethod
    def _remove(path):
        if path and os.path.exists(path):
            os.remove(path)

    # ---- recovery of an interrupted attempt ---------------------------------------------------------------------

    async def _recover(self, db, user, request, run, owner, attempt, manager):
        """Continues the attempt that was under way when the previous worker stopped. Returns (Outcome, skip)
        when it is finished, or (None, providers to skip) when the run should go on with a new attempt."""
        adapter = self.registry.get(attempt.provider)
        paid = adapter.capabilities().paid if adapter else True
        detail = json.loads(attempt.detail or "{}")
        identity = detail.get("identity")
        fallback_from, requested = detail.get("fallback_from"), detail.get("requested")
        ai_runs.set_attempt(db, attempt, recovered=(attempt.recovered or 0) + 1, owner=owner)
        self.log("recovering", run=run.id, attempt=attempt.number, provider=attempt.provider, state=attempt.state,
                 job=attempt.provider_job_id)
        if adapter is None or identity is None:
            raise NeedsAttention(f"The provider {attempt.provider} of this generation is no longer set up on this server; "
                                 "no new generation was started automatically.", "provider_unknown")
        settings = {"model": identity.get("model"), "parameters": identity.get("parameters") or {}}
        digest = attempt.generation_hash
        scope = f"user:{user.id}"
        started = time.time()
        # Made meanwhile by another request? Then that asset answers this run (cache first)
        hit = self.cache.peek(db, user.id, identity, digest) if not request.force_regenerate else None
        if hit and attempt.state == AttemptState.STARTING:
            ai_runs.set_attempt(db, attempt, state=AttemptState.LOST, error_category="interrupted", finished_at=_now())
            candidate = type("C", (), {"name": attempt.provider})()
            return self._hit(db, run, owner, hit, candidate, requested or attempt.provider, request), set()
        lock = self.cache.adopt_lock(db, scope, digest) if not request.force_regenerate else None
        try:
            # 1. Already registered: only the bookkeeping is left
            if attempt.asset_id:
                asset = db.get(models.Asset, attempt.asset_id)
                if asset is not None and asset.status == "ready":
                    ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "finalized_asset"})
                    self.log("recovered", run=run.id, how="asset already registered", asset=asset.id[:8])
                    return self._finalize(db, user, request, run, owner, adapter, attempt, identity, digest, settings, None, None,
                                          fallback_from=fallback_from, requested=requested, started=started, asset=asset), set()
            # 2. The output is on disk: validate and register it
            if attempt.state == AttemptState.REGISTERING and attempt.output_path and os.path.exists(attempt.output_path):
                ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "registered_output"})
                self.log("recovered", run=run.id, how="output on disk registered", attempt=attempt.number)
                return self._finalize(db, user, request, run, owner, adapter, attempt, identity, digest, settings,
                                      attempt.output_path, None, fallback_from=fallback_from, requested=requested,
                                      started=started), set()
            # 3. The provider accepted a job: poll the same job (then fetch it)
            if attempt.provider_job_id and attempt.provider_job:
                if not getattr(adapter, "recoverable", False):
                    return self._lost_or_attention(db, attempt, paid, run, "this provider's jobs cannot be found again")
                try:
                    ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "resumed_job"})
                    self.log("resuming_job", run=run.id, provider=adapter.name, job=attempt.provider_job_id)
                    outcome = await self._resume_attempt(db, user, request, run, owner, adapter, attempt, identity, digest,
                                                         settings, fallback_from, requested, started)
                    self.log("recovered", run=run.id, how="provider job resumed", job=attempt.provider_job_id)
                    return outcome, set()
                except ProviderError as error:
                    self.registry.note_failure(adapter.name, error)
                    if error.category == Failure.LOST:
                        return self._lost_or_attention(db, attempt, paid, run, error.message)
                    if error.category == Failure.CANCELLED:  # cancelled on the provider's side, not by us
                        return self._lost_or_attention(db, attempt, paid, run, "the provider cancelled the job")
                    if not error.fallback_ok:
                        raise self._failed([error], request)
                    if error.retryable and manager:  # temporary trouble: the run waits and comes back (backoff)
                        raise GenerationFailed(503, error.message, category=error.category, retry_after=error.retry_after)
                    return None, {adapter.name}  # this provider failed the job: the next provider may be tried
            # 4. Stopped before the provider's answer was saved: it may or may not have accepted the request
            ai_runs.set_attempt(db, attempt, state=AttemptState.AMBIGUOUS if paid and not run.allow_duplicate else AttemptState.LOST,
                                error_category="ambiguous_submission" if paid else "interrupted", finished_at=_now(),
                                error_message="interrupted before the provider's answer was saved")
            if paid and not run.allow_duplicate:
                raise NeedsAttention("The server stopped while the request was being sent to the provider, so it is not "
                                     "known whether the provider started (and will bill) it. No duplicate generation was "
                                     "started automatically: retry to generate it anyway, or dismiss.", "ambiguous_submission")
            ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "retried_free"})
            self.log("recovered", run=run.id, how="free provider tried again", attempt=attempt.number)
            return None, set()
        finally:
            self.cache.release(db, lock)

    def _lost_or_attention(self, db, attempt, paid, run, why):
        """The provider no longer has the job. Free: try again. Paid: a new request might bill twice — ask first."""
        ai_runs.set_attempt(db, attempt, state=AttemptState.LOST, error_category=Failure.LOST, error_message=why, finished_at=_now())
        if paid and not run.allow_duplicate:
            raise NeedsAttention(f"The provider's job could not be found again ({why}). It may already have been billed, "
                                 "so no new generation was started automatically: retry to generate it again, or dismiss.",
                                 "job_lost")
        ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "retried_free"})
        self.log("recovered", run=run.id, how="lost job of a free provider tried again", attempt=attempt.number)
        return None, set()

    # ---- cancellation, leases, attention ----------------------------------------------------------------------------

    async def _heartbeat(self, run_id, owner, task):
        cfg = self.cfg()
        while True:
            await asyncio.sleep(cfg["heartbeat"])
            db = SessionLocal()
            try:
                if not ai_runs.renew(db, run_id, owner, cfg["lease"]):
                    self.cancel_flags[run_id] = "lease"  # another worker owns it now: stop writing
                    task.cancel()
                    return
                status = db.query(models.AIGenerationRun.status).filter(models.AIGenerationRun.id == run_id).scalar()
                if status == RunState.CANCEL_REQUESTED and run_id not in self.cancel_flags:
                    self.cancel_flags[run_id] = "user"
                    task.cancel()
                    return
            except Exception:  # noqa: BLE001 - a missed heartbeat only shortens the lease
                db.rollback()
            finally:
                db.close()

    def _expire_lease(self, db, run_id, owner):
        try:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id, models.AIGenerationRun.lease_owner == owner,
                                                    models.AIGenerationRun.status.in_(list(ai_runs.OWNED))).update(
                {"lease_expires_at": _now()}, synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    async def _cancel_now(self, db, run_id, owner):
        """Finishes a cancellation: the provider's job is cancelled where the provider supports it (and it is
        said so when it does not); nothing new is submitted."""
        db.expire_all()
        run = db.get(models.AIGenerationRun, run_id)
        attempt = ai_runs.open_attempt(db, run_id)
        note = "Cancelled before anything was sent to a provider."
        if attempt is not None:
            adapter = self.registry.get(attempt.provider)
            handle = json.loads(attempt.provider_job or "null") if attempt.provider_job else None
            if handle and adapter is not None and getattr(adapter, "cancel_supported", False):
                stopped = await asyncio.to_thread(adapter.cancel_handle, handle)
                answer = "cancelled" if stopped else "failed"
                note = "Cancelled; the provider's job was stopped." if stopped else \
                    "Cancelled here; the provider could not confirm that its job stopped."
            elif handle:
                answer = "unsupported"
                note = "Cancelled here; this provider cannot cancel a job, so it may still finish (and bill) on its side."
            else:
                answer = "not_submitted"
            ai_runs.set_attempt(db, attempt, state=AttemptState.CANCELLED, finished_at=_now(),
                                detail_update={"provider_cancel": answer})
        done = ai_runs.transition(db, run_id, RunState.CANCELLED, from_states=ai_runs.OWNED + (RunState.QUEUED,), owner=owner,
                                  finished_at=_now(), lease_owner=None, lease_expires_at=None, error_category=Failure.CANCELLED,
                                  error_message=note, http_status=499)
        if done:
            self.log("cancelled", run=run_id, note=note)
        return done

    def request_cancel(self, db, run):
        """The user's cancel: a queued run stops at once; a running one is told (its worker, here or in another
        process, or recovery if its worker died, finishes the cancel)."""
        if run.status == RunState.QUEUED and ai_runs.transition(
                db, run.id, RunState.CANCELLED, from_states=(RunState.QUEUED,), finished_at=_now(), lease_owner=None,
                lease_expires_at=None, error_category=Failure.CANCELLED, http_status=499,
                error_message="Cancelled before it started; nothing was sent to a provider."):
            return "cancelled"
        if not ai_runs.transition(db, run.id, RunState.CANCEL_REQUESTED, from_states=(RunState.RUNNING, RunState.RECOVERING),
                                  cancel_requested_at=_now()):
            return None
        task = self.tasks.get(run.id)
        if task is not None:
            self.cancel_flags[run.id] = "user"
            task.cancel()
        return "cancelling"

    def cancel(self, db, run):
        """Phase 8 name: cancels a run (True when the cancel was accepted)."""
        return self.request_cancel(db, run) is not None

    def resolve_attention(self, db, run, action):
        """A person's decision about a run that needs attention: retry (accepting a possible duplicate) or dismiss."""
        if action == "retry":
            done = ai_runs.transition(db, run.id, RunState.QUEUED, from_states=(RunState.NEEDS_ATTENTION,), allow_duplicate=True,
                                      finished_at=None, error_category=None, error_message=None, http_status=None, not_before=None,
                                      lease_owner=None, lease_expires_at=None)
            if done and self.wake:
                self.wake()
            return done
        if action == "dismiss":
            return ai_runs.transition(db, run.id, RunState.CANCELLED, from_states=(RunState.NEEDS_ATTENTION,),
                                      error_message="Dismissed; no new generation was started.")
        return False

    # ---- lesson batches ---------------------------------------------------------------------------------------------

    def start_batch(self, db, user, requests):
        """Queues one run per request (identical active runs are reused): the recovery manager's workers generate
        them one by one, on the server, surviving a closed browser and a restart. Returns (batch_id, runs)."""
        batch_id = uuid.uuid4().hex
        runs = []
        for request in requests:
            run, attached = self.open_run(db, user, request, owner=None, background=True, batch_id=batch_id, leased=False)
            runs.append(run)
        if self.wake:
            self.wake()
        self.log("batch", batch=batch_id, runs=len(runs))
        return batch_id, runs

    def check_interrupted(self, db, run):
        """With recovery switched off (AI_RECOVERY_ENABLED=0): a queued/running run that no worker runs any more is
        reported interrupted instead of running forever. With recovery on, the recovery manager continues it."""
        if _env_flag(self.env, "AI_RECOVERY_ENABLED", True):
            return run
        if run.status not in (RunState.QUEUED, RunState.RUNNING) or run.id in self.active or run.id in self.tasks:
            return run
        seen = run.heartbeat_at or run.started_at or run.created_at
        if seen and (_now() - seen).total_seconds() > RUN_STALE_SECONDS:
            ai_runs.transition(db, run.id, RunState.FAILED, from_states=(RunState.QUEUED, RunState.RUNNING), finished_at=_now(),
                               lease_owner=None, lease_expires_at=None, error_category="interrupted", http_status=503,
                               error_message="The server stopped before this generation finished. Please try again.")
            db.refresh(run)
        return run

    # ---- answers without a provider -------------------------------------------------------------------------------

    def _hit(self, db, run, owner, hit, candidate, preferred, request):
        fallback_from = preferred if candidate.name != preferred else None
        self.log("cache_hit", media_type=request.media_type, provider=candidate.name, hash=hit.entry.generation_hash[:12],
                 fallback_from=fallback_from, asset=hit.asset.id[:8], run=run.id if run else None)
        if run is not None and owner is not None:
            self._finish(db, run, owner, RunState.COMPLETED, cache_hit=True, provider=candidate.name, model=hit.entry.model,
                         asset_id=hit.asset.id, generation_hash=hit.entry.generation_hash, fallback_from=fallback_from)
            self._completed(db, run)
        return Outcome(asset=hit.asset, cache_hit=True, generated=False, provider=candidate.name, model=hit.entry.model,
                       requested_provider=preferred, fallback_from=fallback_from, run_id=run.id if run else None)

    def _unavailable(self, selection, request):
        first = selection.candidates[0] if selection.candidates else None
        if request.media_type == VIDEO and first and first.name == "manual" and request.force_regenerate:
            return GenerationFailed(400, "Regenerating needs an AI video provider; in the manual workflow, replace the video file yourself.",
                                    category=Failure.UNSUPPORTED)
        if request.provider:
            if first is None or first.provider is None:
                return GenerationFailed(400, f"There is no AI provider called {request.provider!r}.", category=Failure.UNSUPPORTED)
            if not first.supported:
                return GenerationFailed(422, f"{first.provider.label} cannot make this: {first.reason}.", category=Failure.UNSUPPORTED)
        if request.media_type == PRESENTER and not [c for c in selection.candidates if c.supported]:
            return GenerationFailed(503, "AI presenter generation isn't configured yet: no provider on this server can make a presenter "
                                         "speak the lesson's narration. You can continue with the Aadhi mascot, the illustrated Aadhi "
                                         "Teacher, or no presenter.", category=Failure.CONFIGURATION)
        if request.media_type == VIDEO and self.registry.preferred(VIDEO) is None and not request.provider:
            return GenerationFailed(400, "AI Server is offline. Please start the AI Server from the webpage first.",
                                    category=Failure.CONFIGURATION)
        reasons = "; ".join(f"{c.name}: {c.reason}" for c in selection.candidates if c.reason) or "none is set up"
        return GenerationFailed(503, f"No AI {request.media_type} provider can be used right now ({reasons}).",
                                category=Failure.CONFIGURATION)

    @staticmethod
    def _failed(errors, request):
        """One answer for a request every provider failed: the last provider's failure decides the status."""
        last = errors[-1]
        summary = "; ".join(f"{e.provider}: {e.message}" for e in errors)
        what = f"The AI {request.media_type} could not be generated"
        status, text = {
            Failure.RATE_LIMITED: (429, f"{what}: the provider is busy (rate limited). Please retry shortly"),
            Failure.REJECTED: (422, f"{what}: the provider refused this request. Try describing it differently"),
            Failure.TIMEOUT: (504, f"{what}: the provider did not finish in time. Please retry"),
            Failure.CANCELLED: (499, f"{what}: it was cancelled"),
        }.get(last.category, (502, f"{what} right now. Please retry"))
        return GenerationFailed(status, f"{text} ({summary}).", category=last.category, errors=errors,
                                retry_after=last.retry_after)

    # ---- validation and registration -------------------------------------------------------------------------------

    def validate(self, path, request, settings, adapter):
        """Hard checks reject the output (the provider's answer is not usable media); softer ones become
        warnings kept with the asset and shown in Visual Review, where a person judges the picture. Recovered
        output is checked exactly the same way."""
        media_type = media_kind(request.media_type)  # a presenter clip is checked as the video it is
        what = "presenter clip" if request.media_type == PRESENTER else "video" if media_type == VIDEO else "image"

        def reject(message):
            self.stats["invalid_outputs"] += 1
            return ProviderError(Failure.INVALID_OUTPUT, message, provider=adapter.name, retryable=False)

        if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise reject(f"no {what} was produced")
        size = os.path.getsize(path)
        if size < MIN_BYTES:
            raise reject(f"the {what} is only {size} bytes")
        if size > max_bytes_for(media_type if media_type != PRESENTER else VIDEO, self.env):
            raise reject(f"the {what} is larger than allowed")
        try:
            info = inspect_media(path)
        except MediaError as e:
            raise reject(f"not a usable {what}: {e}")
        if info["kind"] != media_type:
            raise reject(f"expected a {what} but got a {info['kind']}")
        width, height = info.get("width"), info.get("height")
        if info.get("verified") and (not width or not height or min(width, height) < MIN_SIDE):
            raise reject(f"the {what} is too small ({width}x{height})")
        if media_type == VIDEO and info.get("verified") and (info.get("duration") or 0) < MIN_VIDEO_SECONDS:
            raise reject(f"the video lasts only {info.get('duration') or 0:.2f} s")
        warnings = []
        wanted = request.aspect_ratio or adapter.usual_aspect(media_type, settings.get("parameters") or {})
        if wanted and width and height and ratio_of(wanted):
            if abs((width / height) / ratio_of(wanted) - 1) > 0.08:
                warnings.append(f"shape {width}x{height} differs from the expected {wanted}")
        if request.duration_seconds and info.get("duration") and abs(info["duration"] / request.duration_seconds - 1) > 0.5:
            warnings.append(f"lasts {info['duration']:.1f} s instead of {request.duration_seconds:g} s")
        if media_type == VIDEO and adapter.capabilities().audio and info.get("has_audio") is False:
            warnings.append("no sound track")
        if request.media_type == PRESENTER:
            warnings.extend((settings.get("parameters") or {}).get("fallbacks") or [])  # expressions / gestures shown differently
        if looks_blank(path, media_type):
            warnings.append("looks blank (one flat colour)")
        return info, warnings

    def _register(self, db, user, request, path, info, identity, digest, provenance_extra):
        """The output as a library asset. Idempotent: a video is adopted in place (the same file is the same
        asset), an image is stored by its content (the same bytes are the same asset)."""
        details = {"prompt": identity["prompt"][:300], "generation": provenance(identity, digest, **provenance_extra)}
        try:
            if request.media_type in (VIDEO, PRESENTER):
                name = os.path.basename(path)
                if request.media_type == PRESENTER:  # which presenter, for which scene, speaking what (never a secret)
                    p = request.presenter or {}
                    details["presenter"] = {"presenter_id": p.get("presenter_id"), "profile_version": p.get("profile_version"),
                                            "behavior": p.get("behavior"), "expression": p.get("expression"), "gesture": p.get("gesture"),
                                            "speech_seconds": (p.get("speech") or {}).get("duration"), "scene_index": request.scene_index,
                                            "lip_sync": bool(self.registry.get(identity["provider"]) and
                                                             self.registry.get(identity["provider"]).capabilities().lip_sync)}
                return self.library.adopt(db, "static", name, owner_id=user.id, file_name=name, details=details,
                                          source="ai-presenter" if request.media_type == PRESENTER else "ai-video")
            asset, _created = self.library.register_file(db, path, owner_id=user.id, source="ai-image", move=False, details=details,
                                                         file_name=f"ai_image_{digest[:12]}.{info.get('ext') or 'jpg'}")
            return asset
        except MediaError as e:
            raise ProviderError(Failure.INVALID_OUTPUT, f"the media could not be added to the library: {e}",
                                provider=identity["provider"], retryable=False)

    # ---- the manual workflow ---------------------------------------------------------------------------------

    def _manual(self, db, user, request, identity_entry, run, owner):
        """The user makes the video and saves it under the name given here (one name per user and request, so
        nobody picks up another user's file); once it is there it is registered."""
        identity, digest, _ = identity_entry
        filename = f"ai_video_{hashlib.sha256(f'user:{user.id}|{digest}'.encode('utf-8')).hexdigest()[:16]}.mp4"
        path = os.path.join(self.static_dir, filename)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            print(f"\n[MANUAL REQUIRED] User needs to manually generate {filename}")
            if run is not None and owner is not None:
                self._finish(db, run, owner, RunState.FAILED, provider="manual", error_category="manual_required", http_status=200,
                             error_message=f"Waiting for the video file {filename}.")
            return Outcome(provider="manual", requested_provider="manual", run_id=run.id if run else None,
                           manual={"status": "manual_required", "filename": filename, "prompt": identity["prompt"]})
        try:
            asset = self.library.adopt(db, "static", filename, owner_id=user.id, source="ai-video", file_name=filename,
                                       details={"prompt": identity["prompt"][:300], "generation": provenance(identity, digest)})
        except Exception as e:  # noqa: BLE001
            raise GenerationFailed(400, f"static_videos/{filename} is not a usable video: {e}", category=Failure.INVALID_OUTPUT)
        self.cache.record_once(db, f"user:{user.id}", asset, identity, digest)
        if run is not None and owner is not None:
            self._finish(db, run, owner, RunState.COMPLETED, provider="manual", asset_id=asset.id, generation_hash=digest)
        return Outcome(asset=asset, cache_hit=False, generated=False, provider="manual", requested_provider="manual",
                       run_id=run.id if run else None, manual={"status": "success", "manual": True})
