"""Phase 20 — the End-to-End Studio's server side: durable lesson writing, the lesson's workflow state and its checkpoints.

It orchestrates what exists (Phase 11 prepared sources, the shared text-model call, Phase 9 durable runs and recovery, the
/save-history lesson save, the lesson batch and its runs, Visual Review records, the editor's data, Phase 17 styles, Phase
18 quality reports, Phase 19 compare-and-set writes, exports) and adds no second generator, state machine or job system.

  POST /api/studio/lessons                  writes a lesson from a prepared source ({document_id, analysis_id}: the Phase 11
                                            lesson input) or from text, with the page's own screenplay prompt, as a durable
                                            run of kind "lesson_script" (leases, heartbeat, cancel, recovery); the lesson is
                                            saved through the same save as /save-history -> {run_id, status}; 429 while
                                            the user already has 2 lessons being written (model calls run in a small
                                            thread pool of their own)
  GET  /api/studio/runs/{run_id}            {run_id, status, stage, project_id, message, created_at}
  POST /api/studio/runs/{run_id}/cancel     stops the writing (idempotent)
  GET  /api/studio/lessons                  the user's lessons (newest first, at most 50) with their stage, lessons still
                                            being written, and whether a lesson writer is set up on this server (a lesson's
                                            summary is derived again only when the lesson, its runs or its exports changed)
  GET  /api/studio/lessons/{project_id}     the lesson's workflow state, derived from what is saved (rules below)
  PUT  /api/studio/lessons/{project_id}/checkpoint   payload.studio.checkpoints[name] (compare-and-set: never overwrites a
                                            save made meanwhile)

Run stages (run.detail.stage): "Writing the lesson" -> "Checking the lesson" -> "Saving the lesson". A run that already saved
its lesson and is resumed after an interruption finishes without asking the model again (its project id is in its detail,
or the lesson that names the run is found); otherwise it asks again. Failures are plain words; the source is never changed.

Lesson stage (first rule that applies):
  draft            the lesson has no scenes to play
  exporting        its newest export is still being recorded or finished
  generating       pictures, clips, renders, presenter clips or backgrounds are being generated for it
  needs_attention  a generation needs a person's decision, a visual's last generation failed and the scene still shows
                   nothing for it, or the current quality check found blocking problems
  completed        its newest finished video was recorded from the lesson exactly as it is now (same fingerprint)
  ready_to_export  the quality check was made on the lesson as it is now and found nothing blocking or in error
  review           otherwise: the lesson is written and waits for review, edits, visuals, quality check or export
"""
import asyncio
import bisect
import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import defer

import ai_runs
import editor_api
import exports
import models
import quality
import source_analysis as A
import styles
import visuals
from ai_runs import LeaseLost, RunState
from database import SessionLocal, get_db
from source_documents import ModelFailed, call_model, model_available
from studio_screenplay import NAME_LIMITS, check_screenplay, parse_screenplay, stand_in, trace

KIND = "lesson_script"
STUDIO_VERSION = 1
PROVIDERS = ("gemini", "openai", "fake")
REAL_PROVIDERS = ("gemini", "openai")
DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "openai": "gpt-4o-mini", "fake": "stand-in"}
MODEL_NAME = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_SYSTEM_PROMPT = 60000
MAX_TEXT = 262144             # the page's own limit for the source text it sends to the Lesson Director
MAX_REQUEST_BYTES = 1_500_000
MAX_REPAIR_ANSWER = 200_000   # characters of an invalid answer sent back for the one repair
MAX_LESSONS = 50
MAX_ACTIVE_RUNS = 2           # lessons one user can have written at once (joining an identical one is always allowed)
WRITER_THREADS = 3            # model calls run in a small pool of their own (never the shared one exports and media use)
ACTIVE_WRITING = (RunState.QUEUED, RunState.RUNNING, RunState.RECOVERING)  # (a run being cancelled is not joined or counted)
STAGE_WRITING, STAGE_CHECKING, STAGE_SAVING = "Writing the lesson", "Checking the lesson", "Saving the lesson"
FAILED_MESSAGE = "We couldn't write the lesson from this content. Your source is safe. Try again."
NO_WRITER = ("No AI lesson writer is set up on this server, so a lesson can't be written from this content right now. "
             "You can still open your lessons or load a lesson file.")
BUSY = "You already have lessons being written. Wait for one to finish, then try again."
UNREADABLE = "The content contains characters that cannot be read. Remove them and try again."
PREPARED_PREFACE = "Here is the source material, prepared and reviewed in the Document Assistant (Aadhi-ready structure):"
TEXT_PREFACE = "Here is the extracted text from the PDF:"  # the page's own words for a source it extracted
REPAIR_TASK = """Your previous answer was not a valid lesson ({error}). Return only the corrected JSON object, with the same content,
in the shape the instructions ask for (subject_name, unit_name, session_number, session_title, concept_map, scenes, companion_sheet).

Previous answer:
{answer}"""
CHECKPOINTS = ("structure", "lesson", "visuals", "quality", "export")
QUALITY_STATUSES = ("good", "review", "attention", "blocked")  # Phase 18 report statuses
MEDIA_KINDS = (None, "ai_media", "manim")  # the runs that make a lesson's media (lesson writing runs are not media)
DERIVED_KEYS = ("source", "visual_direction")  # left out of the lesson fingerprint (the trace; re-derived on every play)
LINK_TOKEN = re.compile(r"([?&])(token|t)=[^&\"'\s\\]*")


class WriterUnavailable(Exception):
    """No lesson writer can be used for this request (plain words for the user in NO_WRITER)."""


class TooManyRuns(Exception):
    """The user already has MAX_ACTIVE_RUNS lessons being written (plain words for the user in BUSY)."""


def _now():
    return datetime.datetime.utcnow()


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _flag(env, name, default=True):
    return (env.get(name) or ("1" if default else "0")).strip().lower() not in ("0", "false", "no", "off")


# ---- lesson identity and studio data -------------------------------------------------------------------------------

def lesson_fingerprint(payload):
    """What the lesson's video shows, as a SHA-256 (64 hex): its scenes (without their source trace), editor data and video
    style, with volatile parts removed (quality.py's normalisation: review timestamps, AI records, signed `url`s; and the
    `token=` / `t=` values of signed or cache-busting links anywhere). An export records it, so a video is known to match
    the lesson as it is now."""
    payload = payload if isinstance(payload, dict) else {}
    scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    # (a scene's visual_direction is left out too: the planner derives it again whenever the lesson is played and the page keeps
    # it, so a replay would change the fingerprint; what it decides reaches the video through the plans and review records)
    shown = {"scenes": [{k: v for k, v in s.items() if k not in DERIVED_KEYS} if isinstance(s, dict) else s for s in scenes],
             "editor": payload.get("editor") if isinstance(payload.get("editor"), dict) else None,
             "cinematic_style": payload.get("cinematic_style") if isinstance(payload.get("cinematic_style"), dict) else None}
    text = json.dumps(quality._norm(quality._stable(shown)), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(LINK_TOKEN.sub(r"\1\2=", text).encode("utf-8")).hexdigest()


def _bounded(value, depth=0):
    """A checkpoint value kept small: scalars (texts up to 300 characters, finite numbers), and objects / lists of at most
    20 entries, two levels deep."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) < 10 ** 12 else None
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, dict) and depth < 2:
        return {str(k)[:40]: _bounded(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list) and depth < 2:
        return [_bounded(v, depth + 1) for v in value[:20]]
    return None


def clean_checkpoint(name, value):
    """One checkpoint's value cleaned ("quality": {status, counts, fingerprint} of the page's Phase 18 report)."""
    entry = _bounded(value) if isinstance(value, dict) else {}
    if name == "quality":
        counts = value.get("counts") if isinstance(value, dict) and isinstance(value.get("counts"), dict) else {}
        entry = {k: v for k, v in entry.items() if k not in ("status", "counts", "fingerprint")}
        if isinstance(value.get("status"), str) and value["status"] in QUALITY_STATUSES:
            entry["status"] = value["status"]
        entry["counts"] = {str(k)[:20]: v for k, v in list(counts.items())[:10]
                           if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 6}
        if isinstance(value.get("fingerprint"), str) and re.fullmatch(r"[0-9a-f]{8,64}", value["fingerprint"]):
            entry["fingerprint"] = value["fingerprint"]
    return entry


def _clean_checkpoints(value):
    out = {}
    if not isinstance(value, dict):
        return out
    for name in CHECKPOINTS:
        entry = value.get(name)
        if not isinstance(entry, dict):
            continue
        clean = clean_checkpoint(name, entry)
        at, fingerprint = entry.get("at"), entry.get("lesson_fingerprint")
        clean["at"] = at[:40] if isinstance(at, str) else None
        clean["lesson_fingerprint"] = fingerprint if isinstance(fingerprint, str) and HEX64.fullmatch(fingerprint) else None
        out[name] = clean
    return out


def _clean_source(value):
    if isinstance(value, dict) and all(isinstance(value.get(k), str) and HEX32.fullmatch(value[k]) for k in ("document_id", "analysis_id")):
        return {"document_id": value["document_id"], "analysis_id": value["analysis_id"]}
    return None


def clean_studio(value):
    """payload.studio cleaned to its contract — {version: 1, generated: {run_id, provider, model, at, source}, checkpoints:
    {name: {at, lesson_fingerprint, ...}}} — or None when nothing valid is left (what /save-history keeps)."""
    if not isinstance(value, dict):
        return None
    out = {"version": STUDIO_VERSION}
    generated = value.get("generated")
    if isinstance(generated, dict) and isinstance(generated.get("run_id"), str) and HEX32.fullmatch(generated["run_id"]):
        provider, model, at = generated.get("provider"), generated.get("model"), generated.get("at")
        out["generated"] = {"run_id": generated["run_id"], "provider": provider if provider in PROVIDERS else None,
                            "model": model if isinstance(model, str) and MODEL_NAME.fullmatch(model) else None,
                            "at": at[:40] if isinstance(at, str) else None, "source": _clean_source(generated.get("source"))}
    out["checkpoints"] = _clean_checkpoints(value.get("checkpoints"))
    return out if out.get("generated") or out["checkpoints"] else None


# ---- the lesson writing run ------------------------------------------------------------------------------------------

class LessonScriptService:
    """Durable lesson writing (run kind "lesson_script"), modelled on source_documents.DocumentService: the run holds the
    whole request (prompt, source text, names, provider, model, style), so any worker can continue it."""
    kind = KIND

    def __init__(self, save_lesson, env=None, log=None):
        self.save_lesson = save_lesson  # server.save_lesson(db, user, fields) -> Project (the /save-history save)
        self.env = os.environ if env is None else env
        self.log_enabled = _flag(self.env, "AI_MEDIA_LOG") if log is None else log
        self.instance = uuid.uuid4().hex[:12]
        self.tasks, self.active, self.cancel_flags = {}, set(), {}
        self.wake = None
        try:
            threads = max(1, min(8, int(self.env.get("LESSON_WRITER_THREADS") or WRITER_THREADS)))
        except ValueError:
            threads = WRITER_THREADS
        self.pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="lesson-writer")

    def log(self, event, **fields):
        if self.log_enabled:
            print("[STUDIO] " + json.dumps({"event": event, **{k: v for k, v in fields.items() if v is not None}}, sort_keys=True, default=str))

    # ---- which writer -------------------------------------------------------------------------------------------------

    def stand_in_server(self):
        return self.env.get("AI_FAKE_PROVIDER") == "1"

    def writer(self):
        """Whether a lesson can be written here: {available, provider, stand_in} (no key, no reason that names a secret)."""
        real = next((p for p in REAL_PROVIDERS if model_available(p, self.env)[0]), None)
        provider = real or ("fake" if self.stand_in_server() else None)
        return {"available": provider is not None, "provider": provider, "stand_in": provider == "fake"}

    def choose(self, provider, model):
        """(provider, model) that writes this lesson. Test servers only (AI_FAKE_PROVIDER=1): a provider that is omitted,
        unknown or not set up is replaced by the stand-in ("fake"), recorded as such on the run and in the lesson.
        Elsewhere an unknown provider is ValueError (422) and one that is not set up WriterUnavailable (503)."""
        if provider not in PROVIDERS:
            if provider is not None and not self.stand_in_server():
                raise ValueError("provider must be gemini or openai.")
            provider = "fake" if self.stand_in_server() else "gemini"
        available, _reason = model_available(provider, self.env)
        if not available and self.stand_in_server():
            provider, available = "fake", True
        if not available:
            raise WriterUnavailable()
        return provider, (DEFAULT_MODELS["fake"] if provider == "fake" else model or DEFAULT_MODELS[provider])

    # ---- runs ---------------------------------------------------------------------------------------------------------

    def start(self, db, user, request):
        """(run, attached): the identical lesson request still being written (a double click), or a new run, started now.
        TooManyRuns when the user already has MAX_ACTIVE_RUNS lessons being written."""
        digest = hashlib.sha256(json.dumps({"kind": KIND, **request}, sort_keys=True).encode("utf-8")).hexdigest()
        mine = (db.query(models.AIGenerationRun)
                .filter(models.AIGenerationRun.scope_key == f"user:{user.id}", models.AIGenerationRun.kind == KIND,
                        models.AIGenerationRun.status.in_(list(ACTIVE_WRITING)))
                .order_by(models.AIGenerationRun.created_at.desc()).all())
        existing = next((r for r in mine if r.request_hash == digest), None)
        if existing is not None:
            return existing, True
        if len(mine) >= MAX_ACTIVE_RUNS:
            raise TooManyRuns()
        owner = ai_runs.worker_token(self.instance)
        t = _now()
        run = models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{user.id}", user_id=user.id, media_type="text", kind=KIND,
                                     status=RunState.RUNNING, requested_provider=request["provider"], provider=request["provider"],
                                     model=request["model"], instance=self.instance, created_at=t, started_at=t, heartbeat_at=t,
                                     request=json.dumps(request, ensure_ascii=False), request_hash=digest, recovery_count=0, attempts=1,
                                     allow_duplicate=False, lease_owner=owner,
                                     lease_expires_at=t + datetime.timedelta(seconds=ai_runs.settings(self.env)["lease"]),
                                     detail=json.dumps({"purpose": KIND, "stage": STAGE_WRITING, "attempts": []}))
        db.add(run)
        db.commit()
        self.log("lesson_script_started", run=run.id, provider=run.provider, model=run.model, chars=len(request["text"]),
                 source=bool(request.get("source")))
        self.spawn(run.id, owner)
        return run, False

    def spawn(self, run_id, owner, manager=False):
        task = asyncio.get_running_loop().create_task(self.execute(run_id, owner, manager=manager))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self.tasks.pop(rid, None))
        return task

    def _stand_in_answer(self, request, repair):
        """The stand-in's answer (test servers only). FAKE_LLM_MODE: ok | malformed_once | malformed | fail (as the Phase 11
        stand-in); FAKE_LLM_SECONDS: a delay per answer."""
        mode = self.env.get("FAKE_LLM_MODE", "ok")
        time.sleep(float(self.env.get("FAKE_LLM_SECONDS") or 0))
        if mode == "fail":
            raise ModelFailed("the stand-in model is failing on purpose")
        if mode == "malformed" or (mode == "malformed_once" and not repair):
            return "{not json"
        return json.dumps(stand_in(request["text"], request.get("names")), ensure_ascii=False)

    def write(self, request):
        """The lesson from the model (or the stand-in), checked against the page's format, with one bounded repair."""
        provider, model, system = request["provider"], request["model"], request["system_prompt"]

        def ask(task, repair=False):
            if provider == "fake":
                return self._stand_in_answer(request, repair)
            # a whole lesson: the output room the page's own /generate-script gives Gemini (65,536 tokens), and time to write it
            return call_model(provider, model, system, task, self.env, max_tokens=65536, timeout=600)
        answer = ask((PREPARED_PREFACE if request.get("source") else TEXT_PREFACE) + "\n" + request["text"])
        try:
            return check_screenplay(parse_screenplay(answer))
        except A.MalformedOutput as first:
            again = ask(REPAIR_TASK.format(error=str(first)[:200], answer=(answer or "")[:MAX_REPAIR_ANSWER]), repair=True)
            try:
                return check_screenplay(parse_screenplay(again))
            except A.MalformedOutput as second:
                raise A.MalformedOutput(f"the lesson was not valid after one repair ({second})") from None

    @staticmethod
    def prepare(lesson, request):
        """The written lesson made ready to save: every scene traced to the source and given a scene id."""
        trace(lesson["scenes"], request["text"])
        editor_api.ensure_ids(lesson["scenes"])
        return lesson

    def fields(self, run, request, lesson):
        """What the shared lesson save receives (the /save-history fields, plus studio data)."""
        names = request.get("names") or {}
        for key in NAME_LIMITS:
            if names.get(key):
                lesson[key] = names[key]  # the teacher's own names come first
        source = request.get("source")
        return {**lesson, "source_document": source, "cinematic_style": request.get("cinematic_style"),
                "editor": {"version": editor_api.EDITOR_VERSION,
                           "generated_order": [s["scene_id"] for s in lesson["scenes"] if isinstance(s, dict) and s.get("scene_id")]},
                "studio": {"version": STUDIO_VERSION, "checkpoints": {},
                           "generated": {"run_id": run.id, "provider": run.provider, "model": run.model, "at": _iso(_now()), "source": source}}}

    @staticmethod
    def saved_project(db, run, detail):
        """The lesson this run already saved (before an interruption), if any: by the project id in its detail, else the
        first of the user's lessons that names the run (searched by its id, however many lessons were saved since; later
        versions saved from it name the run too)."""
        project_id = detail.get("project_id")
        if isinstance(project_id, int):
            project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == run.user_id).first()
            if project is not None:
                return project
        named = (db.query(models.Project).filter(models.Project.user_id == run.user_id, models.Project.json_data.contains(run.id))
                 .order_by(models.Project.id).all())
        for project in named:
            try:
                studio = json.loads(project.json_data or "{}").get("studio") or {}
            except (TypeError, ValueError, AttributeError):
                continue
            if isinstance(studio, dict) and (studio.get("generated") or {}).get("run_id") == run.id:
                return project
        return None

    def _stage(self, db, run_id, owner, detail, stage):
        detail["stage"] = stage
        ai_runs.update_owned(db, run_id, owner, detail=json.dumps(detail), last_checked_at=_now())  # LeaseLost when taken over

    async def execute(self, run_id, owner, manager=False, quiet=True, db=None):
        own_db = db is None
        db = db or SessionLocal()
        me = asyncio.current_task()
        self.tasks.setdefault(run_id, me)
        self.active.add(run_id)
        heartbeat = asyncio.ensure_future(ai_runs.heartbeat(run_id, owner, me, self.cancel_flags, self.env))
        try:
            run = db.get(models.AIGenerationRun, run_id)
            if run is None:
                return None
            request = json.loads(run.request or "{}")
            detail = json.loads(run.detail or "{}")
            project = self.saved_project(db, run, detail)
            if project is not None:  # saved before an interruption (even if a cancel came after): never written again
                detail.update(project_id=project.id, stage=STAGE_SAVING)
                self._finish(db, run_id, owner, RunState.COMPLETED, detail=json.dumps(detail))
                self.log("lesson_script_recovered_saved", run=run_id, project=project.id)
                return project.id
            if run.status == RunState.CANCEL_REQUESTED:
                raise asyncio.CancelledError()
            user = db.get(models.User, run.user_id)
            if user is None:
                self._finish(db, run_id, owner, RunState.CANCELLED, error_category="not_wanted", error_message="The account no longer exists.")
                return None
            if request.get("provider") == "fake" and not self.stand_in_server():
                # a stand-in run (test servers only) continued by a server without the stand-in: never written for real
                self.log("lesson_script_stand_in_refused", run=run_id)
                self._finish(db, run_id, owner, RunState.FAILED, error_category="lesson_failed", error_message=FAILED_MESSAGE)
                return None
            if manager:
                self.log("lesson_script_resumed", run=run_id)
            self._stage(db, run_id, owner, detail, STAGE_WRITING)
            loop = asyncio.get_running_loop()
            lesson = await loop.run_in_executor(self.pool, self.write, request)
            self._stage(db, run_id, owner, detail, STAGE_CHECKING)
            lesson = await loop.run_in_executor(self.pool, self.prepare, lesson, request)
            self._stage(db, run_id, owner, detail, STAGE_SAVING)
            project = self.save_lesson(db, user, self.fields(run, request, lesson))
            detail["project_id"] = project.id
            self._finish(db, run_id, owner, RunState.COMPLETED, detail=json.dumps(detail))
            self.log("lesson_script_completed", run=run_id, project=project.id, scenes=len(lesson["scenes"]))
            return project.id
        except LeaseLost:
            self.log("lesson_script_lease_lost", run=run_id)
            return None
        except asyncio.CancelledError:
            why = self.cancel_flags.get(run_id)
            db.rollback()
            if why == "user" or (why is None and db.query(models.AIGenerationRun.status).filter(
                    models.AIGenerationRun.id == run_id).scalar() == RunState.CANCEL_REQUESTED):
                try:
                    self._finish(db, run_id, owner, RunState.CANCELLED, error_category="cancelled",
                                 error_message="The lesson writing was stopped. Your source is safe.")
                except LeaseLost:
                    pass
            elif why != "lease":  # the server is stopping: recovery continues at once on the next start
                db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id, models.AIGenerationRun.lease_owner == owner).update(
                    {"lease_expires_at": _now()}, synchronize_session=False)
                db.commit()
            if not quiet:
                raise
            return None
        except Exception as e:  # noqa: BLE001 - the model failed, its answer was not a lesson, or the save was refused
            db.rollback()
            self.log("lesson_script_failed", run=run_id, reason=type(e).__name__)
            try:
                self._finish(db, run_id, owner, RunState.FAILED, error_category="lesson_failed", error_message=FAILED_MESSAGE)
            except LeaseLost:
                pass
            return None
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

    def request_cancel(self, db, run):
        """Stops a run: at once while it waits (queued), else its worker stops it ("cancelling"). None when it is not active."""
        if run.status == RunState.QUEUED:
            done = ai_runs.transition(db, run.id, RunState.CANCELLED, from_states=(RunState.QUEUED,), finished_at=_now(),
                                      error_category="cancelled", error_message="The lesson writing was stopped. Your source is safe.")
            return "cancelled" if done else None
        if not ai_runs.transition(db, run.id, RunState.CANCEL_REQUESTED, from_states=(RunState.RUNNING, RunState.RECOVERING),
                                  cancel_requested_at=_now()):
            return None
        task = self.tasks.get(run.id)
        if task is not None:
            self.cancel_flags[run.id] = "user"
            task.cancel()
        return "cancelling"

    @staticmethod
    def view(run):
        detail = json.loads(run.detail or "{}")
        message = None
        if run.status in (RunState.FAILED, RunState.CANCELLED, RunState.NEEDS_ATTENTION):
            message = run.error_message
        elif run.status == RunState.RECOVERING:
            message = "The server was interrupted while this lesson was being written; Aadhi is picking it up again."
        project_id = detail.get("project_id")
        return {"run_id": run.id, "status": run.status, "stage": detail.get("stage"),
                "project_id": project_id if isinstance(project_id, int) else None, "message": message, "created_at": _iso(run.created_at)}


# ---- the lesson's workflow state (derived) ---------------------------------------------------------------------------

def _hidden(scene):
    edit = scene.get("edit") if isinstance(scene, dict) else None
    return isinstance(edit, dict) and edit.get("hidden") is True


def _edited(scene):
    edit = scene.get("edit") if isinstance(scene, dict) else None
    return isinstance(edit, dict) and (bool(edit.get("original")) or edit.get("origin") in ("inserted", "duplicated", "split"))


def _moved(scenes, order):
    """Scenes the editor moved from where they were written (as editor.js counts them: outside the longest run that kept
    its order)."""
    position = {}
    for i, sid in enumerate(order if isinstance(order, list) else []):
        if isinstance(sid, str) and sid not in position:
            position[sid] = i
    sequence = [position[s["scene_id"]] for s in scenes if isinstance(s, dict) and s.get("scene_id") in position]
    tails = []
    for p in sequence:
        k = bisect.bisect_left(tails, p)
        tails[k:k + 1] = [p]
    return len(sequence) - len(tails)


def _has_media(request, usable):
    """Whether the slot shows a picture, clip or render: a library asset this user can use (usable(asset_id)), or a file the
    scene names directly."""
    previous = request.previous if isinstance(request.previous, dict) else {}
    review = request.review if isinstance(request.review, dict) else {}
    assets = [a for a in (request.explicit_asset_id, previous.get("asset_id"), review.get("asset_id")) if isinstance(a, str) and a]
    return any(usable(a) for a in assets) or bool(request.existing_url or previous.get("url"))


def media_state(db, user, project_id, scenes, library=None, planner=None):
    """The lesson's media from its scenes, Visual Review records and generation runs: the visuals that need a picture, clip
    or render (main / side slots of visible scenes), which have one (with the library: an asset that still exists and is
    usable by this user), and the newest run of each slot (generating, needing a person, failed). Presenter clips and
    backgrounds count through their runs while generating or needing a person; one that failed is listed but not counted
    (the lesson plays with its fallback). A visual removed in Visual Review ("continue without") needs nothing. Items list
    what is not ready. With a planner (the Visual Router: planner(db, user, scenes) -> {(scene_index, slot): plan}), a visual
    the router can already show (a library match, an earlier result, a cached generation) is ready too: the page's own plans
    are not stored with the lesson."""
    routed = {}
    if planner is not None:
        try:
            routed = planner(db, user, scenes) or {}
        except Exception:  # noqa: BLE001 - the counts then come from what is stored, as without a planner
            routed = {}

    def router_shows(index, slot):
        plan = routed.get((index, slot))
        return plan is not None and not getattr(plan, "requires_generation", False) and bool(getattr(plan, "asset_id", None) or getattr(plan, "url", None))
    runs = (db.query(models.AIGenerationRun)
            .filter(models.AIGenerationRun.scope_key == f"user:{user.id}", models.AIGenerationRun.project_id == project_id,
                    or_(models.AIGenerationRun.kind.is_(None), models.AIGenerationRun.kind.in_([k for k in MEDIA_KINDS if k])))
            .order_by(models.AIGenerationRun.created_at.desc()).limit(500).all())
    latest = {}
    for run in runs:  # newest first: the newest run of each scene slot counts
        request = visuals.run_request(run)
        if run.scene_index is None and not request.get("scene_id"):
            index = None  # a run for the whole lesson (e.g. its background)
        else:
            index = visuals.scene_for_run(scenes, request)
            if index is None:
                continue  # its scene was removed
        latest.setdefault((index, run.slot), run)
    counts = {"needed": 0, "ready": 0, "generating": 0, "attention": 0, "failed": 0}
    items, seen = [], set()

    def item(index, slot, status, run=None):
        scene = scenes[index] if isinstance(index, int) and 0 <= index < len(scenes) and isinstance(scenes[index], dict) else {}
        items.append({"scene_index": index, "scene_id": scene.get("scene_id"), "slot": slot, "status": status,
                      "run_id": run.id if run is not None else None,
                      "message": run.error_message if run is not None and status in ("attention", "failed") else None})

    checked = {}

    def usable(asset_id):
        if library is None:
            return True
        if asset_id not in checked:
            asset = library.accessible(db, asset_id.lower(), user.id)
            checked[asset_id] = asset is not None and asset.status == "ready"
        return checked[asset_id]

    def run_status(run):
        if run.status in ai_runs.ACTIVE:
            return "generating"
        if run.status == RunState.NEEDS_ATTENTION:
            return "attention"
        return "failed" if run.status == RunState.FAILED else None

    for request in visuals.requests_from_scenes(scenes):
        if request.slot not in visuals.STORED_SLOTS or _hidden(scenes[request.scene_index]):
            continue
        decision = (request.review or {}).get("status")
        if decision == "removed":
            continue
        if request.renderer is not None or not (request.generation_prompt or request.manim_code):
            continue  # drawn by the page (skill tree, chart, graph, gif...) or nothing to make
        seen.add((request.scene_index, request.slot))
        run = latest.get((request.scene_index, request.slot))
        status = run_status(run) if run is not None else None
        if status in ("generating", "attention"):
            counts[status] += 1
            item(request.scene_index, request.slot, status, run)
        elif _has_media(request, usable) or router_shows(request.scene_index, request.slot):
            counts["ready"] += 1
        elif status == "failed":
            counts["failed"] += 1
            item(request.scene_index, request.slot, "failed", run)
        else:
            counts["needed"] += 1
            item(request.scene_index, request.slot, "needed")
    for (index, slot), run in latest.items():
        if (index, slot) in seen or (isinstance(index, int) and _hidden(scenes[index])):
            continue
        status = run_status(run)  # presenter clips, backgrounds, a visual slot the scene no longer asks for
        if status in ("generating", "attention"):
            counts[status] += 1
            item(index, slot, status, run)
        elif status == "failed" and slot in ("presenter", "background"):
            item(index, slot, status, run)  # listed (to retry), not counted: the lesson plays with its fallback presenter / background
    return {**counts, "items": items[:400]}, review_counts(scenes)


REVIEW_STATUSES = ("pending", "approved", "changed", "removed")


def review_counts(scenes):
    """Visual Review's own counts (review.js reviewItems / summarize): every scene visual it lists — each main / side slot
    that has a visual plan, or that the scene asks for (Visual Review plans the lesson before it opens) — with the plan's
    review status, else the slot's review record, else "needs review"."""
    review = {status: 0 for status in REVIEW_STATUSES}
    asked = {(r.scene_index, r.slot) for r in visuals.requests_from_scenes(scenes) if r.slot in ("main", "side")}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        plans = scene.get("visual_plan") if isinstance(scene.get("visual_plan"), dict) else {}
        records = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
        for slot in ("main", "side"):
            plan = plans.get(slot)
            if not plan and (index, slot) not in asked:
                continue
            status = plan.get("review_status") if isinstance(plan, dict) else None
            if status not in REVIEW_STATUSES:
                record = records.get(slot) if isinstance(records.get(slot), dict) else {}
                status = record.get("status") if record.get("status") in REVIEW_STATUSES else "pending"
            review[status] += 1
    return review


def _exports_state(db, user, project_id, fingerprint):
    count = db.query(models.VideoExport.id).filter(models.VideoExport.user_id == user.id, models.VideoExport.project_id == project_id,
                                                   models.VideoExport.source == "lesson").count()
    newest = exports.latest_lesson_export(db, user.id, project_id)
    done = exports.latest_lesson_export(db, user.id, project_id, completed=True)
    matches = bool(done and done.get("fingerprint") and done["fingerprint"] == fingerprint)
    latest = {"id": newest["id"], "status": newest["status"], "completed_at": newest["completed_at"], "matches_lesson": matches} if newest else None
    return {"count": count, "latest": latest}, {"exporting": bool(newest and newest["status"] in exports.ACTIVE), "matches": matches,
                                               "exported": done is not None}


def derive_stage(scene_count, exporting, media, quality_state, matches):
    """The lesson's stage from its data (the rules in the module docstring, in order)."""
    if not scene_count:
        return "draft"
    if exporting:
        return "exporting"
    if media["generating"]:
        return "generating"
    current_quality = quality_state if quality_state and not quality_state["stale"] else None
    if media["attention"] or media["failed"] or (current_quality and current_quality.get("status") == "blocked"):
        return "needs_attention"
    if matches:
        return "completed"
    if current_quality and current_quality.get("status") in ("good", "review"):
        return "ready_to_export"
    return "review"


def lesson_state(db, user, project, library=None, planner=None):
    """The lesson's workflow state (GET /api/studio/lessons/{project_id}), and facts the lessons list adds
    ({exported}). With the library, a visual counts as ready only while its asset exists and is usable."""
    try:
        payload = json.loads(project.json_data or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    fingerprint = lesson_fingerprint(payload)
    studio = clean_studio(payload.get("studio")) or {}
    checkpoints = studio.get("checkpoints") or {}
    visible = [s for s in scenes if isinstance(s, dict) and not _hidden(s)]
    origin = {"source": 0, "ai": 0, "edited": 0}
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        if _edited(scene):
            origin["edited"] += 1
        elif isinstance(scene.get("source"), dict) and scene["source"].get("origin") in ("source", "ai"):
            origin[scene["source"]["origin"]] += 1
    media, review = media_state(db, user, project.id, scenes, library, planner)
    source = _clean_source(payload.get("source_document"))
    if source:
        doc = db.get(models.SourceDocument, source["document_id"])
        source["file_name"] = doc.file_name if doc is not None and doc.user_id == user.id else None
    style = payload.get("cinematic_style") if isinstance(payload.get("cinematic_style"), dict) else None
    editor = payload.get("editor") if isinstance(payload.get("editor"), dict) else {}
    check = checkpoints.get("quality")
    quality_state = ({"status": check.get("status"), "counts": check.get("counts") or {},
                      "stale": check.get("lesson_fingerprint") != fingerprint} if check else None)
    export_info, export_facts = _exports_state(db, user, project.id, fingerprint)
    names = {key: payload.get(key) if isinstance(payload.get(key), str) else None for key in NAME_LIMITS}
    return {"project_id": project.id, "revision": editor_api.revision_of(project), "fingerprint": fingerprint,
            "title": names["session_title"] or names["subject_name"] or project.subject_name, "names": names,
            "scenes": len(scenes), "hidden": len(scenes) - len(visible), "source": source, "origin": origin, "media": media,
            "review": review, "style": {"style": style.get("style"), "version": style.get("style_version")} if style else None,
            "editor": {"edited": sum(1 for s in scenes if _edited(s)), "moved": _moved(scenes, editor.get("generated_order")),
                       "hidden": len(scenes) - len(visible)},
            "checkpoints": checkpoints, "quality": quality_state, "exports": export_info,
            "stage": derive_stage(len(visible), export_facts["exporting"], media, quality_state, export_facts["matches"])}, export_facts


# ---- the lessons list: summaries kept while nothing they depend on changed ------------------------------------------

_SUMMARIES = OrderedDict()   # (project id, updated_at, live digest) -> the lesson's entry in GET /api/studio/lessons
_SUMMARIES_MAX = 2000
_SUMMARIES_LOCK = threading.Lock()


def _summary_get(key):
    with _SUMMARIES_LOCK:
        entry = _SUMMARIES.get(key)
        if entry is not None:
            _SUMMARIES.move_to_end(key)
        return entry


def _summary_put(key, entry):
    with _SUMMARIES_LOCK:
        _SUMMARIES[key] = entry
        while len(_SUMMARIES) > _SUMMARIES_MAX:
            _SUMMARIES.popitem(last=False)


def _live_signatures(db, user, project_ids):
    """{project id: digest of what a lesson's summary depends on besides its own row}: its media runs (id, status) and its
    lesson exports (id, status, updated_at, timeline output), read for all the listed lessons in three small queries.
    None for a lesson with an export in progress (an abandoned one counts as failed after a while: never kept)."""
    if not project_ids:
        return {}
    facts, busy = defaultdict(list), set()
    for pid, rid, status in (db.query(models.AIGenerationRun.project_id, models.AIGenerationRun.id, models.AIGenerationRun.status)
                             .filter(models.AIGenerationRun.scope_key == f"user:{user.id}", models.AIGenerationRun.project_id.in_(project_ids),
                                     or_(models.AIGenerationRun.kind.is_(None), models.AIGenerationRun.kind.in_([k for k in MEDIA_KINDS if k])))):
        facts[pid].append(("run", rid, status))
    rows = (db.query(models.VideoExport.project_id, models.VideoExport.id, models.VideoExport.status, models.VideoExport.updated_at)
            .filter(models.VideoExport.user_id == user.id, models.VideoExport.project_id.in_(project_ids), models.VideoExport.source == "lesson").all())
    outputs = {}
    if rows:
        outputs = {eid: (status, key) for eid, status, key in db.query(models.ExportOutput.export_id, models.ExportOutput.status,
                                                                         models.ExportOutput.storage_key)
                   .filter(models.ExportOutput.export_id.in_([r[1] for r in rows]), models.ExportOutput.kind == "timeline")}
    for pid, eid, status, updated in rows:
        facts[pid].append(("export", eid, status, _iso(updated), outputs.get(eid)))
        if status in exports.ACTIVE:
            busy.add(pid)
    return {pid: None if pid in busy else hashlib.sha256(json.dumps(sorted(facts.get(pid, [])), default=str).encode()).hexdigest()
            for pid in project_ids}


def _encodable(value):
    """Whether a request's texts can be stored (no lone surrogates and the like)."""
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return True
    except (UnicodeEncodeError, ValueError, TypeError):
        return False


# ---- API --------------------------------------------------------------------------------------------------------------

class CheckpointIn(BaseModel):
    name: str
    value: dict | None = None


def create_studio_router(get_current_user, document_service, save_lesson, library, service=None, planner=None):
    """The Studio's routes. `service` is the LessonScriptService the server registers with recovery (one is made when
    none is given); `library` is the asset library (a lesson's visual is ready only while its asset is usable)."""
    service = service or LessonScriptService(save_lesson)
    router = APIRouter(tags=["studio"])

    def own_project(db, user, project_id):
        project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        return project

    def own_run(db, user, run_id):
        run = db.get(models.AIGenerationRun, run_id) if HEX32.fullmatch(run_id or "") else None
        if run is None or run.scope_key != f"user:{user.id}" or run.kind != KIND:
            raise HTTPException(status_code=404, detail="This lesson writing was not found.")
        return run

    def refuse(message):
        raise HTTPException(status_code=422, detail=message)

    @router.post("/api/studio/lessons")
    async def start(request: Request, current_user=Depends(get_current_user), db=Depends(get_db)):
        """Writes a lesson from a prepared source or from text (a durable run); follow it at /api/studio/runs/{run_id}."""
        too_large = HTTPException(status_code=413, detail="This content is too large to write a lesson from.")
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_REQUEST_BYTES:
            raise too_large
        body = bytearray()
        async for piece in request.stream():  # read in pieces: a body sent without a length stops at the limit too
            body += piece
            if len(body) > MAX_REQUEST_BYTES:
                raise too_large
        try:
            data = json.loads(bytes(body) or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            refuse("The request could not be read.")
        if not _encodable(data):
            refuse(UNREADABLE)  # e.g. a lone surrogate: it could not be stored
        prompt, text, source = data.get("system_prompt"), data.get("text"), data.get("source")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_SYSTEM_PROMPT:
            refuse(f"system_prompt must be a text of 1 to {MAX_SYSTEM_PROMPT} characters.")
        if (text is None) == (source is None):
            refuse("Send the lesson's content: either its text or a prepared source, not both.")
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT):
            refuse(f"text must be a text of 1 to {MAX_TEXT} characters.")
        if source is not None and _clean_source(source) is None:
            refuse("source must name a prepared document: {document_id, analysis_id}.")
        names = data.get("names")
        if names is not None and not isinstance(names, dict):
            refuse("names must be an object.")
        clean_names = {key: re.sub(r"\s+", " ", names[key]).strip()[:limit] for key, limit in NAME_LIMITS.items()
                       if isinstance(names, dict) and isinstance(names.get(key), str) and names[key].strip()}
        provider, model, style = data.get("provider"), data.get("model"), data.get("cinematic_style")
        if provider is not None and not isinstance(provider, str):
            refuse("provider must be gemini or openai.")
        if model is not None and not (isinstance(model, str) and MODEL_NAME.fullmatch(model)):
            refuse("model must be a model name.")
        if style is not None and not isinstance(style, dict):
            refuse("cinematic_style must be an object.")
        try:
            provider, model = service.choose(provider, model)
        except ValueError as e:
            refuse(str(e))
        except WriterUnavailable:
            raise HTTPException(status_code=503, detail=NO_WRITER)
        prepared = None
        if source is not None:
            source = _clean_source(source)
            analysis = db.get(models.DocumentAnalysis, source["analysis_id"])
            if analysis is None or analysis.user_id != current_user.id or analysis.document_id != source["document_id"]:
                raise HTTPException(status_code=404, detail="Source not found.")  # the same answer for other users' sources
            try:
                prepared = document_service.lesson_input(db, analysis)
            except A.DocumentError as e:
                raise HTTPException(status_code=e.status, detail=e.message)
            text = prepared["text"][:MAX_TEXT]
            if not text.strip():
                refuse("The prepared source has no content to teach.")
        try:
            run, attached = service.start(db, current_user, {
                "system_prompt": prompt, "text": text, "source": source, "names": clean_names or None, "provider": provider,
                "model": model, "cinematic_style": styles.lesson_choice(style)})
        except TooManyRuns:
            raise HTTPException(status_code=429, detail=BUSY)
        return {"run_id": run.id, "status": run.status, "attached": attached}

    @router.get("/api/studio/runs/{run_id}")
    def run_view(run_id: str, current_user=Depends(get_current_user), db=Depends(get_db)):
        return service.view(own_run(db, current_user, run_id))

    @router.post("/api/studio/runs/{run_id}/cancel")
    async def cancel(run_id: str, current_user=Depends(get_current_user), db=Depends(get_db)):
        """Stops the lesson writing (nothing is saved); a run that already finished is answered as it is."""
        run = own_run(db, current_user, run_id)
        if run.status in (RunState.QUEUED, RunState.RUNNING, RunState.RECOVERING):
            answer = service.request_cancel(db, run)
            task = service.tasks.get(run.id)
            if answer == "cancelling" and task is not None:
                await asyncio.wait({task}, timeout=5)  # a worker of this process stops within moments; others follow the run
        db.expire_all()
        return service.view(db.get(models.AIGenerationRun, run_id))

    @router.get("/api/studio/lessons")
    def lessons(current_user=Depends(get_current_user), db=Depends(get_db)):
        """The user's lessons, newest first (at most 50), each with its stage; lessons still being written; and whether a
        lesson writer is set up here (so the page can say so before anyone uploads)."""
        projects = (db.query(models.Project).options(defer(models.Project.json_data)).filter(models.Project.user_id == current_user.id)
                    .order_by(models.Project.updated_at.desc(), models.Project.id.desc()).limit(MAX_LESSONS).all())
        live = _live_signatures(db, current_user, [p.id for p in projects])
        out = []
        for project in projects:  # each lesson's summary is derived again only when the lesson or its runs / exports changed
            key = (project.id, _iso(project.updated_at), live.get(project.id))
            entry = _summary_get(key) if key[2] else None
            if entry is None:
                state, facts = lesson_state(db, current_user, project)  # (its saved JSON is read only here)
                entry = {"project_id": project.id, "title": state["title"], "updated_at": _iso(project.updated_at), "stage": state["stage"],
                         "scene_count": state["scenes"], "generating": state["media"]["generating"],
                         "attention": state["media"]["attention"] + state["media"]["failed"], "exported": facts["exported"]}
                if key[2]:
                    _summary_put(key, entry)
            out.append(dict(entry))
        writing = (db.query(models.AIGenerationRun)
                   .filter(models.AIGenerationRun.scope_key == f"user:{current_user.id}", models.AIGenerationRun.kind == KIND,
                           models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE) + [RunState.NEEDS_ATTENTION]))
                   .order_by(models.AIGenerationRun.created_at.desc()).limit(10).all())
        return {"lessons": out, "runs": [{k: v for k, v in service.view(r).items() if k in ("run_id", "status", "stage", "message", "created_at")}
                                         for r in writing], "writer": service.writer()}

    @router.get("/api/studio/lessons/{project_id}")
    def lesson(project_id: int, current_user=Depends(get_current_user), db=Depends(get_db)):
        return lesson_state(db, current_user, own_project(db, current_user, project_id), library, planner)[0]

    @router.put("/api/studio/lessons/{project_id}/checkpoint")
    def checkpoint(project_id: int, body: CheckpointIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """Keeps a stage's checkpoint in the lesson (value null removes it), written with the compare-and-set lesson write:
        a save made meanwhile (the editor's, a review decision, a finished generation) is kept. Each checkpoint records when
        it was made and the lesson fingerprint it is for: value.fingerprint (64 hex, the lesson the page checked) when given,
        else the lesson as saved then. A later change of the lesson makes it stale."""
        if body.name not in CHECKPOINTS:
            refuse("name must be structure, lesson, visuals, quality or export.")
        project = own_project(db, current_user, project_id)
        value = clean_checkpoint(body.name, body.value) if body.value is not None else None
        # the lesson the page checked: value.fingerprint, when it is a lesson fingerprint (64 hex), is what the checkpoint is
        # for (a lesson changed since the page read it makes the checkpoint stale at once); else the lesson as it is saved
        given = (body.value or {}).get("fingerprint")
        seen = given if isinstance(given, str) and HEX64.fullmatch(given) else None
        kept = {}

        def mutate(payload):
            studio = clean_studio(payload.get("studio")) or {"version": STUDIO_VERSION, "checkpoints": {}}
            checkpoints = studio.setdefault("checkpoints", {})
            if value is None:
                if body.name not in checkpoints:
                    kept.update(checkpoints)
                    return False
                del checkpoints[body.name]
            else:
                checkpoints[body.name] = {**value, "at": _iso(_now()), "lesson_fingerprint": seen or lesson_fingerprint(payload)}
            payload["studio"] = studio
            kept.clear()
            kept.update(checkpoints)
            return True

        revision = editor_api.update_lesson(db, project, mutate) or editor_api.revision_of(project)
        return {"revision": revision, "checkpoints": kept}

    return router
