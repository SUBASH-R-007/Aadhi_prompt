"""Phase 22 — rendered lesson export: a saved lesson made into a 1920x1080, 30 fps MP4 on the server, frame by frame.

The live stage stays the only renderer: render_worker.mjs opens the lesson's own page in headless Chrome (render mode,
virtual clock), captures every frame and joins them; this module runs that worker as a durable run, mixes the sound from
what the page logged (export_outputs.mix_render) and saves the result as an ordinary lesson export (exports.py), so the
Videos panel, its history and "Matches the current lesson" work as for a recorded video.

  POST /api/exports/render   {project_id, missing_visuals: refuse | omit, page_settings?, retry_of_id?}
                             -> the export (status RECORDING) with `render` progress; follow it at GET /api/exports/{id};
                             cancel it with PATCH /api/exports/{id} {status: CANCELLED}
                             429 while the user already has a video rendering; 503 when this server cannot render

A render is a run of kind "lesson_render" on the Phase 9 runs (ai_runs: lease, heartbeat, recovery by ai_recovery), modelled on
studio.LessonScriptService. The VideoExport is the side record: request_hash = the export's id; run.project_id stays empty (the
editor counts a lesson's active runs as media being generated and would refuse scene moves meanwhile).

  phase       what happens                                                    after an interruption (restart, lost lease)
  preparing   the worker opens the lesson, checks for missing visuals,        starts again
              plans the scene ranges, prepares the clips' frame sets
  capturing   one page per range, frames into H.264 segments                  finished ranges are kept (ranges/range-<i>.mp4
                                                                              + .json); only unfinished ranges are rendered
                                                                              again (a changed lesson: everything again)
  mixing      the sound and the final MP4 (export_outputs.mix_render)         mixed again from the kept video and timeline
  finishing   stored as <EXPORTS_DIR>/<user>/<export>.mp4 and completed       completed from the stored file
              through exports._complete_export (subtitles, chapters, the
              Phase 20 lesson link), then the library entry (make_mp4)

The worker gets a fixed argument list and nothing secret in argv or its environment: the job and a short-lived normal login
token (renewed while it runs) go through its stdin. It runs below normal priority in a Windows Job Object that kills node,
Chrome and ffmpeg together when the render stops or the server dies (the Manim pattern). Renders have their own slots
(RENDER_MAX_CONCURRENT, default 1): a render picked up by recovery does not hold one of the shared AI recovery slots for hours.

Workspace: <RENDER_DIR>/jobs/job-<export id>/ (RENDER_DIR defaults to <EXPORTS_DIR>/render, so the final move stays on one disk):
plan.json, ranges/, video.mp4, timeline.full.json (the full render timeline: sounds, notes and the page's sync events), the mix.
After success the large files go and the timelines stay for RENDER_KEEP_DAYS; a failed or cancelled render's folder goes.
Frame sets are cached in <RENDER_DIR>/frames/ and dropped after RENDER_FRAMES_KEEP_DAYS without use.
"""
import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import ai_runs
import editor_api
import export_outputs
import exports
import models
import studio
from ai_runs import AttemptState, LeaseLost, RunState
from database import SessionLocal, get_db
from media import probe

KIND = exports.RENDER_KIND  # "lesson_render"
REPO = os.path.dirname(os.path.abspath(__file__))
FPS = 30
EXIT_OK, EXIT_MISSING, EXIT_PAGE, EXIT_CANCELLED = 0, 3, 4, 5
PAGE_SETTINGS_BYTES = 16384
STORAGE_SETTINGS = ("aadhi.cinematic", "aadhi.presenter", "aadhi_ai_visuals")
AI_VISUALS_MODES = ("images", "all", "off")
SETTING_TEXT = re.compile(r"^[A-Za-z0-9._:\- ]{1,80}$")
WORKSPACE_ID = re.compile(r"^[0-9a-f]{32}$")
CANCELLED_MESSAGE = exports.RENDER_CANCELLED
FAILED_MESSAGE = "The video could not be rendered. Your lesson is unchanged; try again."
BUSY = "A video of yours is already being rendered. Wait for it to finish (or cancel it), then try again."
SERVER_BUSY = "Too many videos are waiting to be rendered on this server. Try again in a few minutes."
WAITING = "Waiting for another video to finish rendering"
PAUSED = "Paused while the server restarts; the render continues when it is back"


class RenderFailed(Exception):
    """A render that cannot finish: `code` (error_code for the page), `message` (plain words), `missing` (scenes)."""

    def __init__(self, code, message, missing=None, detail=None, keep=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing = missing or []
        self.detail = detail
        self.keep = keep  # the captured video stays: a retry of this export only mixes and saves again


def _now():
    return datetime.datetime.utcnow()


def _int(env, name, default, low=None, high=None):
    try:
        value = int(float(env.get(name) or default))
    except ValueError:
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _flag(env, name, default=True):
    value = (env.get(name) or "").strip().lower()
    return default if not value else value not in ("0", "false", "no", "off")


def render_dir(env=None):
    env = os.environ if env is None else env
    return os.path.abspath(env.get("RENDER_DIR") or os.path.join(exports.EXPORTS_DIR, "render"))


def settings(env=None):
    env = os.environ if env is None else env
    return {"enabled": _flag(env, "RENDER_ENABLED", True), "dir": render_dir(env),
            "node": env.get("RENDER_NODE") or shutil.which("node"),
            "worker": os.path.abspath(env.get("RENDER_WORKER") or os.path.join(REPO, "render_worker.mjs")),
            "ranges": _int(env, "RENDER_RANGES", 3, 1, 8), "parallel": _int(env, "RENDER_PARALLEL", 0, 0, 8) or None,
            "max_concurrent": _int(env, "RENDER_MAX_CONCURRENT", 1, 1, 8), "max_per_user": _int(env, "RENDER_MAX_PER_USER", 1, 1, 20),
            "max_queued": _int(env, "RENDER_MAX_QUEUED", 20, 1, 1000), "max_minutes": _int(env, "RENDER_MAX_MINUTES", 180, 1, 1440),
            "timeout": _int(env, "RENDER_TIMEOUT_SECONDS", 12 * 3600, 60), "stall": _int(env, "RENDER_STALL_SECONDS", 180, 2),
            "quality": _int(env, "RENDER_JPEG_QUALITY", 92, 92, 100), "crf": _int(env, "RENDER_CRF", 18, 0, 51),
            "token_minutes": _int(env, "RENDER_TOKEN_MINUTES", 30, 5, 24 * 60), "keep_days": _int(env, "RENDER_KEEP_DAYS", 7, 0),
            "frames_keep_days": _int(env, "RENDER_FRAMES_KEEP_DAYS", 14, 1), "base_url": (env.get("RENDER_BASE_URL") or "").rstrip("/"),
            "frames_max_bytes": int(float(env.get("RENDER_FRAMES_MAX_GB") or 5) * 1024 ** 3),
            "min_free_gb": float(env.get("RENDER_MIN_FREE_GB") or 2), "keep_failed_hours": _int(env, "RENDER_KEEP_FAILED_HOURS", 24, 0),
            "prepare_stall": _int(env, "RENDER_PREPARE_STALL_SECONDS", 900, 60),
            # lesson (the default): every range page pre-rolls the lesson from its start, so any scene boundary can be a seam (the
            # page is deterministic: a 2-page and a 1-page render of one lesson differ only by encoder noise, >= 43.8 dB);
            # scene: one scene of pre-roll, seams only where the page marks them safe (RENDER_PREROLL=scene, the fallback)
            "preroll": "scene" if (env.get("RENDER_PREROLL") or "").strip().lower() == "scene" else "lesson",
            "browser": env.get("RENDER_BROWSER") or "chrome",
            "premade": os.path.abspath(env.get("RENDER_PREMADE_FRAMES") or os.path.join(REPO, "assets", "mascot_frames"))}


def clean_page_settings(value):
    """The teacher's playback settings a render page restores (Phase 22 whitelist): {storage: {localStorage key: value},
    tts: {engine, voice, geminiVoice, rate}}, or {} when none. ValueError (422) for anything else or more than 16 KB."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("page_settings must be an object.")
    try:
        text = json.dumps(value, allow_nan=False, separators=(",", ":"), ensure_ascii=False)  # measured as the page measures it
    except (TypeError, ValueError):
        raise ValueError("page_settings must be plain JSON.")
    if len(text.encode("utf-8")) > PAGE_SETTINGS_BYTES:
        raise ValueError("page_settings is too large.")
    unknown = set(value) - set(STORAGE_SETTINGS) - {"tts_engine", "voice", "gemini_voice", "rate"}
    if unknown:
        raise ValueError(f"page_settings cannot carry {', '.join(sorted(unknown))}.")
    storage, tts = {}, {}
    for key in ("aadhi.cinematic", "aadhi.presenter"):
        if value.get(key) is not None:
            if not isinstance(value[key], dict):
                raise ValueError(f"page_settings.{key} must be an object.")
            storage[key] = value[key]
    mode = value.get("aadhi_ai_visuals")
    if mode is not None:
        if mode not in AI_VISUALS_MODES:
            raise ValueError("page_settings.aadhi_ai_visuals must be images, all or off.")
        storage["aadhi_ai_visuals"] = mode
    for key, name in (("tts_engine", "engine"), ("voice", "voice"), ("gemini_voice", "geminiVoice")):
        if value.get(key) is not None:
            if not isinstance(value[key], str) or not SETTING_TEXT.fullmatch(value[key]):
                raise ValueError(f"page_settings.{key} is not a valid choice.")
            tts[name] = value[key]
    rate = value.get("rate")
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.5 <= rate <= 1.5:
            raise ValueError("page_settings.rate must be between 0.5 and 1.5.")
        tts["rate"] = round(float(rate), 2)
    out = {}
    if storage:
        out["storage"] = storage
    if tts:
        out["tts"] = tts
    return out


FAMILY_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")


def render_layout(page_settings, payload):
    """Which layout the render page draws, for the planner's rates: {kind: classic | cinematic, key}. Cinematic Studio when the
    teacher's settings say so ('aadhi.cinematic' mode, as cinematic.js isClassic reads it), keyed by the lesson's style
    family (its own cinematic_style, else the settings' style) when there is one."""
    cine = ((page_settings or {}).get("storage") or {}).get("aadhi.cinematic")
    if not isinstance(cine, dict) or cine.get("mode") != "cinematic":
        return {"kind": "classic", "key": "classic"}
    own = (payload or {}).get("cinematic_style") if isinstance(payload, dict) else None
    family = (own or {}).get("style") if isinstance(own, dict) else None
    family = family or cine.get("style")
    return {"kind": "cinematic", "key": f"cinematic:{family}" if isinstance(family, str) and FAMILY_NAME.fullmatch(family) else "cinematic"}


def missing_message(missing):
    """Plain words naming the scenes without a visual (the refusal of a render that may not leave them out)."""
    names = []
    for item in (missing or [])[:8]:
        if isinstance(item, dict):
            index, title = item.get("index"), str(item.get("title") or "").strip()
            label = f"Scene {index + 1}" if isinstance(index, int) else "A scene"
            names.append(f'{label} "{title[:60]}"' if title else label)
        elif isinstance(item, str) and item.strip():
            names.append(item.strip()[:100])
    more = len(missing or []) - len(names)
    listing = "; ".join(names) + (f"; and {more} more" if more > 0 else "")
    count = len(missing or [])
    return (f"Not rendered: {count} scene{'s' if count != 1 else ''} {'have' if count != 1 else 'has'} no visual yet ({listing}). "
            "Choose Render without these visuals to leave them out, or add the visuals first.")


# ---- the worker process (one per render attempt) ---------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    import ctypes.wintypes as wt

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD),
                    ("SchedulingClass", wt.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class ProcessTree:
    """The worker and everything it starts (Chrome, ffmpeg), stopped together. Windows: a Job Object with kill-on-close (the
    handle closes when this server process dies too, so nothing outlives it), as manim_sandbox does; elsewhere a process
    group (start_new_session), plus every process whose command line names the render's workspace (Chrome's profile)."""

    def __init__(self, workspace=None):
        self.job = None
        self.workspace = workspace
        if os.name == "nt":
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wt.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
            k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
            k32.CloseHandle.argtypes = [wt.HANDLE]
            self.k32 = k32
            job = k32.CreateJobObjectW(None, None)
            if job:
                limit = _EXT_LIMIT()
                limit.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
                if k32.SetInformationJobObject(job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
                    self.job = job
                else:
                    k32.CloseHandle(job)
        self.proc = None

    def popen_flags(self):
        if os.name == "nt":
            return {"creationflags": 0x08000000 | 0x00004000}  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        return {"start_new_session": True}

    def add(self, proc):
        self.proc = proc
        if self.job is not None and not self.k32.AssignProcessToJobObject(self.job, int(proc._handle)):
            proc.kill()
            raise RenderFailed("unavailable", "The renderer could not be started safely on this server.")
        if self.job is None and os.name == "nt":
            proc.kill()
            raise RenderFailed("unavailable", "The renderer could not be started safely on this server.")

    def kill(self):
        if self.job is not None:
            self.k32.TerminateJobObject(self.job, 1)
            return
        import signal
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                self.proc.kill()
        # Playwright starts Chrome in a process group of its own off Windows: whatever still runs from the workspace goes too
        if self.workspace and os.path.isdir("/proc"):
            marker = self.workspace.encode()
            for pid in filter(str.isdigit, os.listdir("/proc")):
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        if marker in f.read():
                            os.kill(int(pid), signal.SIGKILL)
                except (OSError, ValueError):
                    pass

    def close(self):
        self.kill()
        if self.job is not None:
            self.k32.CloseHandle(self.job)
            self.job = None


class Progress:
    """What the worker reported (its stderr lines), read by the run's progress loop. Thread-safe; never holds the token."""

    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "preparing"
        self.message = "Preparing the lesson for rendering"
        self.plan = None          # {ranges, scenes}
        self.scenes = []          # [{index, hidden}] from plan.json
        self.ranges = {}          # index -> {frames, scene, done}
        self.kept = {}            # index -> frames
        self.lesson = None
        self.missing = []
        self.error = None         # {code, message, detail}
        self.done = None
        self.warnings = 0
        self.browser = None
        self.seams = None
        self.notes = []
        self.pages = []
        self.plan_warnings = []
        self.last_event = time.time()

    def event(self, data):
        with self.lock:
            self.last_event = time.time()
            kind = data.get("type")
            if kind == "progress":
                self.phase = data.get("phase") if data.get("phase") in ("preparing", "capturing", "joining") else self.phase
                self.message = str(data.get("message") or self.message)[:200]
            elif kind == "plan":
                self.plan = {"ranges": data.get("ranges") or [], "scenes": data.get("scenes")}
            elif kind == "kept":
                for item in data.get("ranges") or []:
                    if isinstance(item, dict) and isinstance(item.get("index"), int):
                        self.kept[item["index"]] = int(item.get("frames") or 0)
            elif kind == "frames":
                if isinstance(data.get("range"), int):
                    self.phase = "capturing"
                    entry = self.ranges.setdefault(data["range"], {"frames": 0, "scene": None, "done": False})
                    entry["frames"] = int(data.get("frames") or 0)
                    entry["scene"] = data.get("scene") if isinstance(data.get("scene"), int) else entry["scene"]
            elif kind == "range_done":
                if isinstance(data.get("range"), int):
                    entry = self.ranges.setdefault(data["range"], {"frames": 0, "scene": None, "done": False})
                    entry.update(frames=int(data.get("frames") or 0), done=True)
                    if isinstance(data.get("timing"), dict):  # predicted against actual seconds, per page (the plan's log)
                        self.pages.append({"page": data["range"], "predicted": data.get("predicted"),
                                           **{k: v for k, v in data["timing"].items() if isinstance(v, (int, float))}})
            elif kind == "lesson":
                self.lesson = export_outputs.clean_lesson_link(data.get("link"))
            elif kind == "missing":
                self.missing = data.get("missing") if isinstance(data.get("missing"), list) else []
            elif kind == "error":
                self.error = {"code": str(data.get("code") or "render_failed")[:40], "message": str(data.get("message") or "")[:300],
                              "detail": str(data.get("detail") or "")[:800] or None}
                if isinstance(data.get("missing"), list):
                    self.missing = data["missing"]
            elif kind == "done":
                self.done = data
            elif kind == "page_warning":
                self.warnings += 1
            elif kind == "browser":
                self.browser = str(data.get("version") or "")[:40]
            elif kind == "seams":  # which scene boundaries the ranges were split at, and why (kept with the attempt)
                self.seams = {k: data.get(k) for k in ("mode", "layout", "safe", "chosen", "balancedBy", "reason", "pages", "predictedSeconds")
                              if data.get(k) is not None}
            elif kind == "note":
                self.notes.append(str(data.get("text") or "")[:300])
            elif kind == "plan_warning":  # a page much slower or faster than planned (the next plan learns from it)
                self.plan_warnings.append({k: data.get(k) for k in ("range", "stage", "measuredFps", "plannedFps", "message")})

    def snapshot(self):
        with self.lock:
            frames = sum(self.kept.values()) + sum(r["frames"] for i, r in self.ranges.items() if i not in self.kept)
            ranges = (self.plan or {}).get("ranges") or []
            played = [s for s in self.scenes if not s.get("hidden")]
            total = len(played) or 0
            done = 0
            for r in ranges:
                if not isinstance(r, dict):
                    continue
                inside = [s["index"] for s in played if s["index"] >= (r.get("fromScene") or 0)
                          and (r.get("toScene") is None or s["index"] <= r["toScene"])]
                state = self.ranges.get(r.get("index"))
                if r.get("index") in self.kept or (state and state["done"]):
                    done += len(inside)
                elif state and isinstance(state["scene"], int):
                    done += len([i for i in inside if i < state["scene"]])
            return {"phase": self.phase, "message": self.message, "frames": frames, "scenes_done": done, "scenes_total": total,
                    "lesson": self.lesson, "missing": list(self.missing), "error": dict(self.error) if self.error else None,
                    "done": dict(self.done) if self.done else None, "warnings": self.warnings, "browser": self.browser,
                    "last_event": self.last_event, "kept": dict(self.kept), "seams": self.seams, "notes": list(self.notes[:20]),
                    "pages": list(self.pages), "plan_warnings": list(self.plan_warnings)}


def _clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---- the service ---------------------------------------------------------------------------------------------------------

class RenderService:
    """Durable lesson renders (run kind "lesson_render"); see the module docstring."""
    kind = KIND

    def __init__(self, library, register_asset=None, make_token=None, env=None, log=None):
        self.library = library
        self.register_asset = register_asset  # (db, storage_key, export) -> asset: the finished video in the library
        self.make_token = make_token          # (username, minutes, run_id) -> a normal login token for the render page
        self.env = os.environ if env is None else env
        self.log_enabled = _flag(self.env, "AI_MEDIA_LOG") if log is None else log
        self.instance = uuid.uuid4().hex[:12]
        self.tasks, self.active, self.cancel_flags = {}, set(), {}
        self.wake = None
        cfg = settings(self.env)
        self.slots = threading.BoundedSemaphore(cfg["max_concurrent"])
        # a running render's worker is followed in a thread of its own pool: renders waiting for a slot wait on the event loop
        # (no thread), and a render never holds one of the threads the rest of the server shares
        self.pool = ThreadPoolExecutor(max_workers=cfg["max_concurrent"], thread_name_prefix="render-worker")
        self.progress = {}  # run id -> Progress of its attempt running here
        self.heartbeats = {}  # run id -> its heartbeat task

    def log(self, event, **fields):
        if self.log_enabled:
            print("[RENDER] " + json.dumps({"event": event, **{k: v for k, v in fields.items() if v is not None}}, sort_keys=True, default=str))

    def cfg(self):
        return settings(self.env)

    # ---- availability, workspace -------------------------------------------------------------------------------------------

    def availability(self):
        """(True, "") when this server can render, else (False, plain reason)."""
        cfg = self.cfg()
        if not cfg["enabled"]:
            return False, "Rendering videos is switched off on this server. Use Record the screen instead."
        if not os.path.isfile(cfg["worker"]):
            return False, "The renderer is not installed on this server. Use Record the screen instead."
        if not cfg["worker"].endswith(".py"):
            if not cfg["node"]:
                return False, "This server has no Node.js, which rendering needs. Use Record the screen instead."
            if not (os.path.isfile(os.path.join(REPO, "node_modules", "playwright-core", "package.json"))
                    or self.env.get("PLAYWRIGHT_CORE_DIR")):
                return False, "The render tools (playwright-core) are not installed on this server. Use Record the screen instead."
        if not shutil.which("ffprobe"):
            return False, "This server has no ffprobe, which rendering needs. Use Record the screen instead."
        reason = export_outputs.mp4_unavailable_reason()
        if reason:
            return False, "This server's ffmpeg cannot make MP4 videos. Use Record the screen instead."
        return True, ""

    def workspace(self, export_id):
        if not WORKSPACE_ID.fullmatch(export_id or ""):
            raise ValueError("invalid export id")
        return os.path.join(self.cfg()["dir"], "jobs", f"job-{export_id}")

    @staticmethod
    def _remove(path):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    def _trim_workspace(self, ws):
        """After success: the large files go, the plan and the timelines stay (RENDER_KEEP_DAYS)."""
        for name in ("video.mp4", "final.mp4", "video.part.mp4", "temp", "mix"):
            target = os.path.join(ws, name)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.isfile(target):
                os.remove(target)
        ranges = os.path.join(ws, "ranges")
        if os.path.isdir(ranges):
            for name in os.listdir(ranges):
                if name.endswith(".mp4"):
                    try:
                        os.remove(os.path.join(ranges, name))
                    except OSError:
                        pass

    # ---- starting a render ---------------------------------------------------------------------------------------------------

    def start(self, db, user, project, missing_visuals, page_settings, base_url, retry_of_id=None):
        """The export (RECORDING) and its run, owned here and started at once. RenderFailed("busy") over the limits."""
        cfg = self.cfg()
        active = (db.query(models.AIGenerationRun.scope_key).filter(models.AIGenerationRun.kind == KIND,
                                                                    models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).all())
        if sum(1 for (scope,) in active if scope == f"user:{user.id}") >= cfg["max_per_user"]:
            raise RenderFailed("busy", BUSY)
        if len(active) >= cfg["max_queued"]:
            raise RenderFailed("busy", SERVER_BUSY)
        title = " ".join(filter(None, [project.subject_name, project.session_number, project.session_title]))[:200] or "Lesson"
        export = models.VideoExport(id=uuid.uuid4().hex, project_id=project.id, user_id=user.id, title=title, source="lesson",
                                    status="RECORDING", stage="Waiting to render", progress=None, retry_of_id=retry_of_id)
        db.add(export)
        owner = ai_runs.worker_token(self.instance)
        t = _now()
        request = {"export_id": export.id, "project_id": project.id, "missing_visuals": missing_visuals, "page_settings": page_settings,
                   "base_url": base_url, "width": 1920, "height": 1080, "fps": FPS, "ranges": cfg["ranges"]}
        run = models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{user.id}", user_id=user.id, media_type="video", kind=KIND,
                                     status=RunState.RUNNING, requested_provider="render", provider="chrome", instance=self.instance,
                                     created_at=t, started_at=t, heartbeat_at=t, request=json.dumps(request), request_hash=export.id,
                                     project_id=None, recovery_count=0, attempts=0, allow_duplicate=False, lease_owner=owner,
                                     lease_expires_at=t + datetime.timedelta(seconds=ai_runs.settings(self.env)["lease"]),
                                     detail=json.dumps({"purpose": KIND, "phase": "preparing", "stage": "Waiting to render",
                                                        "frames_done": 0, "frames_total": None}))
        db.add(run)
        db.commit()
        db.refresh(export)
        if retry_of_id:
            self._adopt(db, retry_of_id, export.id, missing_visuals)
        self.log("render_started", run=run.id, export=export.id, project=project.id, missing=missing_visuals)
        self.spawn(run.id, owner)
        return export, run

    def _adopt(self, db, earlier_id, export_id, missing_visuals):
        """A retry of a render that failed after its capture (the mix, or saving) takes over the frames it kept: only the
        mix and saving are done again. Only when that capture made the same choice about missing visuals, or had none
        (the lesson version is checked as for any resumed render)."""
        earlier = db.get(models.VideoExport, earlier_id)
        if earlier is None or earlier.status not in ("FAILED", "CANCELLED") or not WORKSPACE_ID.fullmatch(earlier_id):
            return False
        old = self.workspace(earlier_id)
        plan = self._read_json(os.path.join(old, "plan.json"))
        if not plan or not os.path.isdir(os.path.join(old, "ranges")):
            return False
        run = exports.render_runs(db, [earlier]).get(earlier_id)
        earlier_choice = json.loads(run.request or "{}").get("missing_visuals") if run else None
        if plan.get("missing") and earlier_choice != missing_visuals:
            return False
        try:
            os.replace(old, self.workspace(export_id))
        except OSError:
            return False
        self.log("render_adopted", export=export_id, earlier=earlier_id)
        return True

    def spawn(self, run_id, owner, manager=False):
        """Starts the render task. Returns a task the recovery manager may mark as its own: a separate, finished one, so a
        render that lasts hours never holds one of the shared AI recovery slots (renders wait for their own slots)."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(self.execute(run_id, owner, manager=manager))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self.tasks.pop(rid, None) if self.tasks.get(rid) is _t else None)
        if not manager:
            return task
        return loop.create_task(asyncio.sleep(0))

    # ---- one attempt ---------------------------------------------------------------------------------------------------------

    def _finish(self, db, run_id, owner, state, **fields):
        # the heartbeat stops first: once the run is finished it would find no lease and cancel this task in its last steps
        heartbeat = self.heartbeats.pop(run_id, None)
        if heartbeat is not None:
            heartbeat.cancel()
        sources = tuple(s for s in ai_runs.OWNED + (RunState.QUEUED,) if state in ai_runs.TRANSITIONS[s])
        if not ai_runs.transition(db, run_id, state, from_states=sources, owner=owner, finished_at=_now(), lease_owner=None,
                                  lease_expires_at=None, **fields):
            raise LeaseLost(run_id)

    def _detail(self, db, run_id, owner, **fields):
        run = db.get(models.AIGenerationRun, run_id)
        db.refresh(run)
        detail = json.loads(run.detail or "{}")
        detail.update(fields)
        ai_runs.update_owned(db, run_id, owner, detail=json.dumps(detail), last_checked_at=_now())
        return detail

    def _export_progress(self, db, export_id, stage=None, progress=None, keep_alive=False):
        export = db.get(models.VideoExport, export_id)
        if export is None:
            return None
        db.refresh(export)
        if export.status not in exports.ACTIVE:
            return export
        if stage is not None:
            export.stage = stage[:255]
        export.progress = progress
        if keep_alive:
            export.updated_at = _now()  # a long render keeps the export away from the stale-export cleanup
        db.commit()
        return export

    def _phase(self, db, run_id, owner, export_id, phase, message, progress=None, **fields):
        self._detail(db, run_id, owner, phase=phase, stage=message, **fields)
        self._export_progress(db, export_id, message, progress, keep_alive=True)

    async def execute(self, run_id, owner, manager=False):
        db = SessionLocal()
        me = asyncio.current_task()
        self.tasks.setdefault(run_id, me)
        self.active.add(run_id)
        heartbeat = asyncio.ensure_future(ai_runs.heartbeat(run_id, owner, me, self.cancel_flags, self.env))
        self.heartbeats[run_id] = heartbeat
        export_id = None
        ws = None
        try:
            run = db.get(models.AIGenerationRun, run_id)
            if run is None:
                return None
            request = json.loads(run.request or "{}")
            export_id = request.get("export_id")
            export = db.get(models.VideoExport, export_id) if export_id else None
            if export is None or export.status not in exports.ACTIVE:
                self._finish(db, run_id, owner, RunState.CANCELLED, error_category="cancelled", error_message="The video export was stopped.")
                return None
            if run.status == RunState.CANCEL_REQUESTED:
                self.cancel_flags[run_id] = "user"
                raise asyncio.CancelledError()
            user = db.get(models.User, run.user_id)
            project = db.query(models.Project).filter(models.Project.id == request.get("project_id"),
                                                      models.Project.user_id == run.user_id).first()
            if user is None or project is None:
                raise RenderFailed("lesson_missing", "The lesson no longer exists, so the video could not be rendered.")
            ws = self.workspace(export.id)
            os.makedirs(ws, exist_ok=True)
            fingerprint, link = self._lesson_link(project)
            point, attempt = self._resume_point(db, run, owner, ws, export, fingerprint)
            self.log("render_attempt", run=run_id, export=export.id, resume=point, manager=manager or None)
            if point in ("start", "ranges"):
                self._detail(db, run_id, owner, lesson=link)  # the version being captured (a resumed mix keeps saying so)
                await self._capture(db, run, owner, export, user, project, ws, attempt, fingerprint)
            if point != "register":
                await self._mix(db, run, owner, export, user, ws, attempt)
            await self._register(db, run, owner, export, ws, attempt, link)
            return export.id
        except RenderFailed as failure:
            db.rollback()
            self._fail(db, run_id, owner, export_id, failure)
            if ws and not failure.keep:
                await asyncio.to_thread(self._remove, ws)
            return None
        except LeaseLost:
            self.log("render_lease_lost", run=run_id)
            return None
        except asyncio.CancelledError:
            why = self.cancel_flags.get(run_id)
            db.rollback()
            user_stop = why == "user" or (why is None and db.query(models.AIGenerationRun.status).filter(
                models.AIGenerationRun.id == run_id).scalar() == RunState.CANCEL_REQUESTED)
            if not user_stop and why is None and export_id:
                exp = db.get(models.VideoExport, export_id)
                user_stop = exp is not None and exp.status not in exports.ACTIVE
            if user_stop:
                try:
                    self._finish(db, run_id, owner, RunState.CANCELLED, error_category="cancelled", error_message=CANCELLED_MESSAGE,
                                 detail=json.dumps({**json.loads(db.get(models.AIGenerationRun, run_id).detail or "{}"),
                                                    "phase": "cancelled", "error_code": "cancelled", "stage": CANCELLED_MESSAGE}))
                except LeaseLost:
                    pass
                self._settle_export(db, export_id, "CANCELLED", CANCELLED_MESSAGE)
                if ws:
                    await asyncio.to_thread(self._remove, ws)
            elif why != "lease":  # the server is stopping: recovery continues at once on the next start
                db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id, models.AIGenerationRun.lease_owner == owner).update(
                    {"lease_expires_at": _now()}, synchronize_session=False)
                db.commit()
                if export_id:
                    self._export_progress(db, export_id, PAUSED, None, keep_alive=True)
                if ws:  # Chrome's throwaway profile (it held the render's login) is never left behind; the frames stay
                    shutil.rmtree(os.path.join(ws, "temp"), ignore_errors=True)
            return None
        except Exception as e:  # noqa: BLE001 - whatever went wrong, the export ends in plain words
            db.rollback()
            self.log("render_failed", run=run_id, reason=type(e).__name__, detail=str(e)[:300])
            self._fail(db, run_id, owner, export_id, RenderFailed("render_failed", FAILED_MESSAGE, detail=str(e)[:300]))
            if ws and not os.path.isfile(os.path.join(ws, "video.mp4")):  # a finished capture stays for a retry
                await asyncio.to_thread(self._remove, ws)
            return None
        finally:
            heartbeat.cancel()
            if self.heartbeats.get(run_id) is heartbeat:
                self.heartbeats.pop(run_id, None)
            self.cancel_flags.pop(run_id, None)
            self.active.discard(run_id)
            self.progress.pop(run_id, None)
            if self.tasks.get(run_id) is me:
                self.tasks.pop(run_id, None)
            db.close()

    def _lesson_link(self, project):
        """(fingerprint, {project_id, fingerprint, revision}) of the saved lesson as it is now (the server's own reading; the
        render page's renderMode.lessonLink() replaces it when it gives one)."""
        try:
            payload = json.loads(project.json_data or "{}")
        except (TypeError, ValueError):
            payload = {}
        fingerprint = studio.lesson_fingerprint(payload if isinstance(payload, dict) else {})
        revision = editor_api.revision_of(project) or "0"
        return fingerprint, export_outputs.clean_lesson_link({"project_id": project.id, "fingerprint": fingerprint, "revision": revision[:40]})

    def _resume_point(self, db, run, owner, ws, export, fingerprint):
        """Where this attempt starts (the table in the module docstring): register | mix | ranges | start, and its attempt."""
        attempt = ai_runs.open_attempt(db, run.id)
        stored = f"{export.user_id}/{export.id}.mp4"
        if attempt is None:
            if not ai_runs.attempts_of(db, run.id):  # a new render: maybe the capture its retried export kept (_adopt)
                plan = self._read_json(os.path.join(ws, "plan.json"))
                if plan and plan.get("fingerprint") == fingerprint:
                    if os.path.isfile(os.path.join(ws, "video.mp4")) and os.path.isfile(os.path.join(ws, "timeline.full.json")):
                        attempt = self._new_attempt(db, run, owner, {"recovered_how": "adopted_capture"})
                        self._detail(db, run.id, owner, recovered_how="adopted_capture")
                        return "mix", attempt
            self._remove_contents(ws)
            attempt = self._new_attempt(db, run, owner)
            return "start", attempt
        ai_runs.set_attempt(db, attempt, recovered=(attempt.recovered or 0) + 1, owner=owner)
        final = os.path.join(ws, "final.mp4")
        if attempt.state == AttemptState.REGISTERING and (os.path.isfile(final) or exports.STORAGE.exists(stored)):
            ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "registered"})
            return "register", attempt
        if os.path.isfile(os.path.join(ws, "video.mp4")) and os.path.isfile(os.path.join(ws, "timeline.full.json")):
            ai_runs.set_attempt(db, attempt, detail_update={"recovered_how": "mixed_again"})
            return "mix", attempt
        plan = self._read_json(os.path.join(ws, "plan.json"))
        kept = 0
        if plan and plan.get("fingerprint") == fingerprint:
            kept = sum(1 for r in plan.get("ranges") or [] if isinstance(r, dict)
                       and os.path.isfile(os.path.join(ws, "ranges", f"range-{r.get('index')}.mp4"))
                       and os.path.isfile(os.path.join(ws, "ranges", f"range-{r.get('index')}.json")))
        changed = bool(plan) and plan.get("fingerprint") != fingerprint
        how = "kept_ranges" if kept else ("rendered_again_changed" if changed else "rendered_again")
        if not kept:
            self._remove_contents(ws)
        ai_runs.set_attempt(db, attempt, state=AttemptState.LOST, error_category="interrupted", finished_at=_now(),
                            error_message="the render was interrupted", detail_update={"recovered_how": how, "kept_ranges": kept})
        attempt = self._new_attempt(db, run, owner, {"resumed_from": attempt.id, "recovered_how": how, "kept_ranges": kept})
        self._detail(db, run.id, owner, recovered_how=how, kept_ranges=kept)
        return ("ranges" if kept else "start"), attempt

    def _new_attempt(self, db, run, owner, detail=None):
        attempt = ai_runs.new_attempt(db, run.id, owner, "render", "chrome", None)
        ai_runs.set_attempt(db, attempt, state=AttemptState.RENDERING, detail_update={"workspace_id": run.request_hash, **(detail or {})})
        db.expire(run)
        ai_runs.update_owned(db, run.id, owner, attempts=(db.get(models.AIGenerationRun, run.id).attempts or 0) + 1)
        return attempt

    @staticmethod
    def _remove_contents(ws):
        for name in os.listdir(ws) if os.path.isdir(ws) else []:
            target = os.path.join(ws, name)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass

    @staticmethod
    def _read_json(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # ---- capture -------------------------------------------------------------------------------------------------------------

    def _base_url(self, request):
        """Where the render page opens this server: RENDER_BASE_URL, else the address this run was started on (the server's
        own listening port, never a client's Host header)."""
        return self.cfg()["base_url"] or request.get("base_url")

    def _job(self, run, request, user, project, ws, fingerprint, token):
        cfg = self.cfg()
        page_settings = request.get("page_settings") or {}
        return {"base": self._base_url(request), "projectId": project.id, "token": token, "workspace": ws, "renderDir": cfg["dir"],
                "premadeDir": cfg["premade"], "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
                "missingVisuals": request.get("missing_visuals") or "refuse", "ranges": request.get("ranges") or cfg["ranges"],
                "parallel": cfg["parallel"], "seed": self.seed(project.id), "quality": cfg["quality"],
                "crf": cfg["crf"], "tailFrames": 45, "maxFrames": cfg["max_minutes"] * 60 * FPS,
                # the worker's own stall checks fire before the server's watchdog, with a better message
                "stallSeconds": max(30, cfg["stall"] - 30), "prepareStallSeconds": cfg["prepare_stall"], "minFreeGb": cfg["min_free_gb"],
                "frameSeconds": 300,  # above the page's own per-frame limits (a request 180 s), which say what got stuck
                "prerollMode": cfg["preroll"], "layout": render_layout(page_settings, self._payload(project)),
                "fingerprint": fingerprint, "channel": cfg["browser"],
                "settings": {"storage": page_settings.get("storage") or {}, "tts": page_settings.get("tts") or None}}

    @staticmethod
    def _payload(project):
        try:
            payload = json.loads(project.json_data or "{}")
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def seed(project_id):
        """The page's random seed: one per lesson, so every page of a render, a retry and a later render of the same lesson
        show the same particles and animations (frames that match across pages, renders and parity checks)."""
        import hashlib
        return int(hashlib.sha256(f"aadhi-render:{project_id}".encode()).hexdigest()[:8], 16) % 2147483647

    def _command(self):
        cfg = self.cfg()
        if cfg["worker"].endswith(".py"):  # a stand-in worker (tests)
            return [sys.executable, cfg["worker"]]
        return [cfg["node"], cfg["worker"]]

    def _environment(self, ws):
        """The worker's environment, built from scratch: what node, Chrome and ffmpeg need to run, temporary files (and so
        Chrome's throwaway profile, which holds the login) inside the workspace; no secrets."""
        temp = os.path.join(ws, "temp")
        os.makedirs(temp, exist_ok=True)
        keep = ("SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA", "LOCALAPPDATA", "APPDATA",
                "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
                "PATHEXT", "COMSPEC", "HOME", "LANG", "PLAYWRIGHT_CORE_DIR", "PLAYWRIGHT_BROWSERS_PATH", "FONTCONFIG_PATH", "XDG_RUNTIME_DIR")
        env = {k: os.environ[k] for k in keep if os.environ.get(k)}
        tools = [os.path.dirname(p) for p in (shutil.which("ffmpeg"), self.cfg()["node"], sys.executable) if p]
        env["PATH"] = os.pathsep.join(dict.fromkeys(tools + os.environ.get("PATH", "").split(os.pathsep)))
        env.update({"TEMP": temp, "TMP": temp, "TMPDIR": temp, "NODE_ENV": "production", "PYTHONIOENCODING": "utf-8"})
        return env

    async def _capture(self, db, run, owner, export, user, project, ws, attempt, fingerprint):
        request = json.loads(run.request or "{}")
        if not self._base_url(request):
            raise RenderFailed("unavailable", "This server does not know its own address for rendering (set RENDER_BASE_URL).")
        progress = Progress()
        plan = self._read_json(os.path.join(ws, "plan.json"))
        if plan:
            progress.scenes = plan.get("scenes") or []
        self.progress[run.id] = progress
        self._phase(db, run.id, owner, export.id, "preparing", "Preparing the lesson for rendering", None)
        if self.make_token is None:
            raise RenderFailed("unavailable", "This server cannot sign the render page in.")
        run_id, username = run.id, user.username  # plain values: the threads below never touch the session
        await self._wait_for_slot(db, run_id, owner, export.id, progress)
        future = None
        try:
            await asyncio.to_thread(self.trim_frame_cache)
            await asyncio.to_thread(self._check_disk)
            token_for = lambda: self.make_token(username, self.cfg()["token_minutes"], run_id)  # noqa: E731
            job = self._job(run, request, user, project, ws, fingerprint, token_for())
            stop = lambda: run_id in self.cancel_flags  # noqa: E731 - "user", "lease" or "stopping"
            progress.event({"type": "progress", "phase": "preparing", "message": "Preparing the lesson for rendering"})
            future = asyncio.get_running_loop().run_in_executor(self.pool, self._run_worker, ws, job, progress, stop, token_for)
        finally:
            if future is None:
                self.slots.release()
            else:
                future.add_done_callback(lambda _f: self.slots.release())  # once the worker's whole tree has stopped
        last_write = 0.0
        try:
            while not future.done():
                await asyncio.wait([future], timeout=1.0)
                if not progress.scenes:
                    plan = self._read_json(os.path.join(ws, "plan.json"))
                    if plan:
                        progress.scenes = plan.get("scenes") or []
                if time.time() - last_write >= 2.0:
                    last_write = time.time()
                    self._report(db, run.id, owner, export.id, progress)
        except asyncio.CancelledError:
            self.cancel_flags.setdefault(run.id, "stopping")
            await self._wait_thread(future)
            raise
        except LeaseLost:
            self.cancel_flags.setdefault(run.id, "lease")
            await self._wait_thread(future)
            raise
        result = future.result()
        snap = progress.snapshot()
        why = self.cancel_flags.get(run.id)
        ai_runs.set_attempt(db, attempt, detail_update={"exit_code": result["code"], "stopped": result.get("stopped"),
                                                        "browser": snap["browser"], "page_warnings": snap["warnings"],
                                                        "frames": snap["frames"], "error": snap["error"], "seams": snap["seams"],
                                                        "worker_notes": snap["notes"], "pages": snap["pages"]})
        if snap["seams"]:
            self.log("render_seams", run=run_id, **snap["seams"])
        for warning in snap["plan_warnings"]:
            self.log("render_plan_off", run=run_id, **{k: v for k, v in warning.items() if v is not None})
        for page in snap["pages"]:
            self.log("render_page", run=run_id, **page)  # predicted against actual wall time
        if why == "user" or result["code"] == EXIT_CANCELLED:
            raise asyncio.CancelledError()
        if why in ("stopping", "lease"):
            raise asyncio.CancelledError()
        if result["code"] == EXIT_OK and os.path.isfile(os.path.join(ws, "video.mp4")):
            if snap["lesson"] and snap["lesson"]["project_id"] == project.id:
                ai_runs.set_attempt(db, attempt, detail_update={"lesson": snap["lesson"]})
            return snap
        error = snap["error"] or {}
        if result["code"] == EXIT_MISSING or error.get("code") == "missing_visuals":
            raise RenderFailed("missing_visuals", missing_message(snap["missing"]), missing=snap["missing"])
        if result.get("stopped") == "stalled":
            raise RenderFailed("render_failed", "The lesson stopped responding while it was being rendered. Your lesson is unchanged; try again.")
        if result.get("stopped") == "timeout":
            raise RenderFailed("render_failed", "The render took longer than this server allows and was stopped.")
        if error.get("code") == "unavailable":
            raise RenderFailed("unavailable", error.get("message") or "This server cannot render videos right now.")
        if error.get("code") == "lesson_changed":
            raise RenderFailed("lesson_changed", "The lesson was changed while it was being rendered. Render it again.")
        if error.get("code") == "too_long":
            raise RenderFailed("too_long", "The lesson is longer than this server renders. Use Record the screen instead.")
        if result["code"] == EXIT_PAGE or error.get("code") == "page_error":
            raise RenderFailed("page_error", (error.get("message") or "The lesson page failed while it was being rendered.")
                               + " Your lesson is unchanged; try again.", detail=error.get("detail"))
        raise RenderFailed("render_failed", FAILED_MESSAGE, detail=(error.get("detail") or error.get("message")))

    async def _wait_for_slot(self, db, run_id, owner, export_id, progress):
        """At most RENDER_MAX_CONCURRENT renders at once on this server; the others wait here, on the event loop (no thread
        held), still reporting (so a cancel from the Videos panel or a lost lease is noticed). Returns holding a slot."""
        waited = 0.0
        while not self.slots.acquire(blocking=False):
            if not waited:
                progress.event({"type": "progress", "phase": "preparing", "message": WAITING})
            if run_id in self.cancel_flags:
                raise asyncio.CancelledError()
            if waited % 2.0 < 0.5:
                self._report(db, run_id, owner, export_id, progress)
                if run_id in self.cancel_flags:
                    raise asyncio.CancelledError()
            await asyncio.sleep(0.5)
            waited += 0.5

    def _check_disk(self):
        cfg = self.cfg()
        os.makedirs(cfg["dir"], exist_ok=True)
        if shutil.disk_usage(cfg["dir"]).free < cfg["min_free_gb"] * 1024 ** 3:
            raise RenderFailed("disk_full", "The server is running out of disk space, so the video could not be rendered. "
                                            "Free some space on the server (or ask its administrator), then render again.")

    def trim_frame_cache(self, protect_after=None):
        """Frame sets above RENDER_FRAMES_MAX_GB go, least recently used first (a set's manifest is touched whenever a
        render uses it); never one used since `protect_after` (a render still running may need it). Returns sets removed."""
        cfg = self.cfg()
        root = os.path.join(cfg["dir"], "frames")
        if not os.path.isdir(root):
            return 0
        if protect_after is None:
            protect_after = time.time() - 6 * 3600
        sets = []
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if ".tmp-" in name or os.path.islink(path) or not os.path.isdir(path):
                continue
            size = 0
            for folder, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(folder, f))
                    except OSError:
                        pass
            manifest = os.path.join(path, "manifest.json")
            used = os.path.getmtime(manifest) if os.path.isfile(manifest) else 0
            sets.append((used, size, path))
        total = sum(size for _used, size, _path in sets)
        removed = 0
        for used, size, path in sorted(sets):
            if total <= cfg["frames_max_bytes"]:
                break
            if used > protect_after:
                continue
            shutil.rmtree(path, ignore_errors=True)
            total -= size
            removed += 1
        if removed:
            self.log("frames_evicted", removed=removed, bytes_left=total)
        return removed

    @staticmethod
    async def _wait_thread(future):
        """A render never outlives the worker that owns it: wait until its thread has stopped the process tree."""
        while not future.done():
            try:
                await asyncio.wait([future])
            except asyncio.CancelledError:
                continue

    def _report(self, db, run_id, owner, export_id, progress):
        """The worker's progress into the run (lease checked: LeaseLost when another worker took over) and the export."""
        snap = progress.snapshot()
        frames = snap["frames"]
        if snap["phase"] == "capturing" or frames:
            so_far = f"{_clock(frames / FPS)} of video so far"
            scenes = f"scene {min(snap['scenes_done'] + 1, snap['scenes_total'])} of {snap['scenes_total']}" if snap["scenes_total"] else ""
            message = "Rendering the video: " + ", ".join(p for p in (scenes, so_far) if p)
            phase = "capturing"
            share = snap["scenes_done"] / snap["scenes_total"] if snap["scenes_total"] else 0.0
            value = round(0.05 + 0.85 * min(share, 1.0), 3)
        elif snap["phase"] == "joining":
            message, phase, value = "Joining the parts of the video", "capturing", 0.9
        else:
            message, phase, value = snap["message"], "preparing", None
        self._detail(db, run_id, owner, phase=phase, stage=message, frames_done=frames, frames_total=None,
                     scenes_done=snap["scenes_done"], scenes_total=snap["scenes_total"])
        export = self._export_progress(db, export_id, message, value, keep_alive=True)
        if export is None or export.status not in exports.ACTIVE:
            self.cancel_flags.setdefault(run_id, "user")  # cancelled from the Videos panel (PATCH): stop the worker now

    def _run_worker(self, ws, job, progress, stop, token_for):
        """Starts the worker (fixed argument list; the job and the token on stdin), follows its stderr lines and stops its
        whole process tree when asked, when it stalls or when it runs too long. Returns {code, stopped}."""
        cfg = self.cfg()
        tree = ProcessTree(ws)
        stopped = None
        try:
            proc = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    cwd=ws, env=self._environment(ws), **tree.popen_flags())
        except OSError as e:
            tree.close()
            raise RenderFailed("unavailable", "The renderer could not be started on this server.", detail=str(e))
        try:
            tree.add(proc)

            def read():
                for raw in proc.stderr:
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("{"):
                        try:
                            progress.event(json.loads(line))
                        except ValueError:
                            pass
            reader = threading.Thread(target=read, name="render-worker-output", daemon=True)
            reader.start()
            proc.stdin.write((json.dumps(job) + "\n").encode("utf-8"))
            proc.stdin.flush()
            job.pop("token", None)
            started = last_token = time.time()
            cancel_sent = None
            while proc.poll() is None:
                time.sleep(0.25)
                now = time.time()
                if stop() and cancel_sent is None:
                    stopped = "cancelled"
                    cancel_sent = now
                    self._send(proc, {"type": "cancel"})
                if cancel_sent is not None and now - cancel_sent > 8:
                    tree.kill()  # it did not stop on its own
                    break
                if cancel_sent is None and now - started > cfg["timeout"]:
                    stopped = "timeout"
                    tree.kill()
                    break
                if cancel_sent is None and now - progress.last_event > cfg["stall"]:
                    stopped = "stalled"
                    tree.kill()
                    break
                if now - last_token > cfg["token_minutes"] * 20:  # a fresh login every third of its lifetime
                    last_token = now
                    self._send(proc, {"type": "token", "token": token_for()})
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tree.kill()
                proc.wait(timeout=10)
            reader.join(timeout=5)
            return {"code": proc.returncode, "stopped": stopped}
        finally:
            tree.close()  # nothing of the render survives it (Chrome included)
            for pipe in (proc.stdin, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass

    @staticmethod
    def _send(proc, message):
        try:
            proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass

    # ---- mix, register -------------------------------------------------------------------------------------------------------

    def resolver(self, db, user_id):
        """src (as the page played it) -> the local file: /static/..., /video_template/..., or a library asset the user may use."""
        def resolve(src):
            path = str(src or "").split("?", 1)[0].split("#", 1)[0]
            path = re.sub(r"^https?://[^/]+", "", path)
            for prefix, volume in (("/static/", "static"), ("/video_template/", "system")):
                if path.startswith(prefix):
                    name = path[len(prefix):]
                    if not name or "/" in name or "\\" in name or name.startswith("."):
                        return None
                    try:
                        return self.library.volumes[volume].path(name)
                    except (KeyError, ValueError):
                        return None
            match = re.fullmatch(r"/api/assets/([0-9a-f]{32})/content", path)
            if match:
                asset = db.get(models.Asset, match.group(1))
                if asset is not None and asset.status == "ready" and asset.owner_id in (None, user_id):
                    try:
                        return self.library.file_path(asset)
                    except (KeyError, ValueError):
                        return None
            return None
        return resolve

    async def _mix(self, db, run, owner, export, user, ws, attempt):
        timeline = self._read_json(os.path.join(ws, "timeline.full.json"))
        if not timeline:
            raise RenderFailed("render_failed", FAILED_MESSAGE, detail="the render timeline is missing")
        frames = int(timeline.get("frames") or 0)
        self._phase(db, run.id, owner, export.id, "mixing", "Mixing the narration and sound", 0.92, frames_done=frames, frames_total=frames)
        try:
            result = await asyncio.to_thread(export_outputs.mix_render, os.path.join(ws, "video.mp4"), timeline,
                                             os.path.join(ws, "final.mp4"), self.resolver(db, user.id), ws)
        except export_outputs.MixError as e:
            # the frames are kept: Render again (or a recovery) only mixes and saves again
            raise RenderFailed("render_failed", "The sound of the video could not be mixed. Your lesson is unchanged; Render again "
                                                "finishes it without rendering the frames again.", detail=str(e), keep=True)
        ai_runs.set_attempt(db, attempt, state=AttemptState.REGISTERING, output_path=os.path.join(ws, "final.mp4"),
                            detail_update={"frames": result["frames"], "duration": result["duration"], "sounds": result["sounds"],
                                           "skipped_sounds": result["skipped"][:20]})
        return result

    async def _register(self, db, run, owner, export, ws, attempt, link):
        self._phase(db, run.id, owner, export.id, "finishing", "Saving the video", 0.97)
        key = f"{export.user_id}/{export.id}.mp4"
        final = os.path.join(ws, "final.mp4")
        if os.path.isfile(final):
            await asyncio.to_thread(self._store, key, final)
        if not exports.STORAGE.exists(key):
            raise RenderFailed("render_failed", FAILED_MESSAGE, detail="the final video is missing")
        path = exports.STORAGE.path(key)
        info = await asyncio.to_thread(probe, path)
        timeline = self._read_json(os.path.join(ws, "timeline.full.json")) or {}
        # the lesson version the frames show: the render page's own reading, else the server's when the capture started
        page_link = export_outputs.clean_lesson_link(json.loads(attempt.detail or "{}").get("lesson") or timeline.get("lesson"))
        captured = export_outputs.clean_lesson_link(json.loads(db.get(models.AIGenerationRun, run.id).detail or "{}").get("lesson"))
        for candidate in (page_link, captured):
            if candidate and candidate["project_id"] == export.project_id:
                link = candidate
                break
        db.refresh(export)
        if export.status not in exports.ACTIVE:  # cancelled while it was being saved
            exports.STORAGE.delete(key)
            raise asyncio.CancelledError()
        frames = int(timeline.get("frames") or 0)
        duration = (info or {}).get("duration") or (frames / FPS if frames else None)
        record = {"lesson": link, "scenes": timeline.get("scenes") or [], "cues": timeline.get("cues") or []}
        exports._complete_export(db, export, "mp4", key, os.path.getsize(path), info, "video/mp4", duration, record)
        ai_runs.set_attempt(db, attempt, state=AttemptState.COMPLETED, finished_at=_now(), output_path=None)
        detail = json.loads(db.get(models.AIGenerationRun, run.id).detail or "{}")
        notes = timeline.get("notes") if isinstance(timeline.get("notes"), list) else []
        detail.update(phase="done", stage="Video ready", frames_done=frames, frames_total=frames, error_code=None,
                      notes=[n for n in notes if isinstance(n, dict)][:50])
        self._finish(db, run.id, owner, RunState.COMPLETED, detail=json.dumps(detail), error_category=None, error_message=None)
        self.log("render_completed", run=run.id, export=export.id, frames=frames)
        await asyncio.to_thread(self._trim_workspace, ws)
        if self.register_asset is not None:
            asyncio.get_running_loop().run_in_executor(None, exports.make_mp4, export.id, self.register_asset)

    @staticmethod
    def _store(key, final):
        try:
            exports.STORAGE.put_file(key, final, move=True)
        except OSError:  # RENDER_DIR on another disk than EXPORTS_DIR
            exports.STORAGE.put_file(key, final, move=False)
            os.remove(final)

    # ---- failure, cancel, reconcile ------------------------------------------------------------------------------------------

    def _fail(self, db, run_id, owner, export_id, failure):
        self.log("render_refused" if failure.code == "missing_visuals" else "render_failed", run=run_id, code=failure.code,
                 detail=failure.detail)
        try:
            run = db.get(models.AIGenerationRun, run_id)
            detail = json.loads(run.detail or "{}") if run else {}
            detail.update(phase="failed", stage=failure.message, error_code=failure.code, missing=failure.missing[:50])
            self._finish(db, run_id, owner, RunState.FAILED, error_category=failure.code[:30], error_message=failure.message,
                         detail=json.dumps(detail))
        except LeaseLost:
            return
        self._settle_export(db, export_id, "FAILED", failure.message)

    @staticmethod
    def _settle_export(db, export_id, status, message):
        export = db.get(models.VideoExport, export_id) if export_id else None
        if export is None:
            return
        db.refresh(export)
        if export.status in exports.ACTIVE:
            export.status, export.error_message, export.stage, export.progress = status, message, None, None
            export.completed_at = _now()
            db.commit()

    def request_cancel(self, db, run):
        """Stops a render (the generic run cancel): at once while queued, else its worker stops it. None when not active."""
        if run.status == RunState.QUEUED:
            done = ai_runs.transition(db, run.id, RunState.CANCELLED, from_states=(RunState.QUEUED,), finished_at=_now(),
                                      error_category="cancelled", error_message=CANCELLED_MESSAGE)
            if done:
                self._settle_export(db, json.loads(run.request or "{}").get("export_id"), "CANCELLED", CANCELLED_MESSAGE)
            return "cancelled" if done else None
        if not ai_runs.transition(db, run.id, RunState.CANCEL_REQUESTED, from_states=(RunState.RUNNING, RunState.RECOVERING),
                                  cancel_requested_at=_now()):
            return None
        task = self.tasks.get(run.id)
        if task is not None:
            self.cancel_flags[run.id] = "user"  # the worker is stopped (its whole process tree) before the task ends
            task.cancel()
        return "cancelling"

    def settle(self, db):
        """Render exports still active whose run ended without them (recovery gave up, or the run was cancelled elsewhere)
        are closed in plain words, so none stays "Recording" until the 24-hour cleanup."""
        open_exports = db.query(models.VideoExport).filter(models.VideoExport.status.in_(list(exports.ACTIVE)),
                                                           models.VideoExport.source == "lesson").all()
        runs = exports.render_runs(db, open_exports)
        return sum(1 for export in open_exports if export.id in runs and exports.settle_render(db, export, runs[export.id]))

    def sweep(self, db):
        """Hourly (recovery manager) and at start-up: reconcile exports, drop workspaces no render needs any more and frame
        sets unused for RENDER_FRAMES_KEEP_DAYS."""
        removed = 0
        try:
            self.settle(db)
        except Exception as e:  # noqa: BLE001 - housekeeping never stops the server
            db.rollback()
            self.log("settle_failed", error=str(e)[:200])
        cfg = self.cfg()
        jobs = os.path.join(cfg["dir"], "jobs")
        now = time.time()
        lease = ai_runs.settings(self.env)["lease"]
        for name in os.listdir(jobs) if os.path.isdir(jobs) else []:
            path = os.path.join(jobs, name)
            export_id = name[len("job-"):] if name.startswith("job-") else ""
            if not WORKSPACE_ID.fullmatch(export_id) or os.path.islink(path) or not os.path.isdir(path):
                continue
            age = now - os.path.getmtime(path)
            export = db.get(models.VideoExport, export_id)
            if export is not None and export.status in exports.ACTIVE:
                continue
            if export is not None and export.status == "COMPLETED":
                keep = cfg["keep_days"] * 86400
            elif export is not None and os.path.isfile(os.path.join(path, "video.mp4")):
                keep = cfg["keep_failed_hours"] * 3600  # a capture kept for Render again (only the mix is redone)
            else:
                keep = max(120, lease * 2)
            if age > keep:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        frames = os.path.join(cfg["dir"], "frames")
        for name in os.listdir(frames) if os.path.isdir(frames) else []:
            path = os.path.join(frames, name)
            if ".tmp-" in name:
                stale = now - os.path.getmtime(path) > 3600
            else:
                manifest = os.path.join(path, "manifest.json")
                stale = not os.path.isfile(manifest) or now - os.path.getmtime(manifest) > cfg["frames_keep_days"] * 86400
            if stale and os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        active = (db.query(models.AIGenerationRun.created_at).filter(models.AIGenerationRun.kind == KIND,
                                                                     models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE)))
                  .order_by(models.AIGenerationRun.created_at).first())
        protect = (active[0] - datetime.datetime.utcnow()).total_seconds() + time.time() if active else None
        removed += self.trim_frame_cache(protect_after=protect)
        if removed:
            self.log("render_swept", removed=removed)
        return removed


# ---- the route --------------------------------------------------------------------------------------------------------------

class RenderRequest(BaseModel):
    project_id: int
    missing_visuals: str = "refuse"
    page_settings: dict | None = None
    retry_of_id: str | None = None


def create_render_router(service, get_current_user):
    router = APIRouter(tags=["exports"])

    @router.post("/api/exports/render")
    async def start_render(body: RenderRequest, request: Request, user: models.User = Depends(get_current_user), db=Depends(get_db)):
        """Renders a saved lesson to an MP4 on the server (Phase 22); the export follows it (GET /api/exports/{id})."""
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if body.missing_visuals not in ("refuse", "omit"):
            raise HTTPException(status_code=422, detail="missing_visuals must be refuse or omit.")
        try:
            page_settings = clean_page_settings(body.page_settings)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if body.retry_of_id:
            earlier = db.query(models.VideoExport).filter(models.VideoExport.id == body.retry_of_id,
                                                          models.VideoExport.user_id == user.id).first()
            if earlier is None:
                raise HTTPException(status_code=404, detail="Export not found")
        available, reason = service.availability()
        if not available:
            raise HTTPException(status_code=503, detail=reason)
        # this server's own listening address (the socket uvicorn bound), never the client's Host header (a proxy, or anyone,
        # could point the render page and its login elsewhere); RENDER_BASE_URL wins when set
        server_addr = request.scope.get("server") or (None, None)
        base = service.cfg()["base_url"] or (f"http://127.0.0.1:{server_addr[1]}" if server_addr[1] else None)
        if not base:
            raise HTTPException(status_code=503, detail="This server does not know its own address for rendering (set RENDER_BASE_URL).")
        try:
            export, _run = service.start(db, user, project, body.missing_visuals, page_settings, base, body.retry_of_id)
        except RenderFailed as e:
            raise HTTPException(status_code=429 if e.code == "busy" else 409, detail=e.message)
        return exports.serialize_finished(db, [export])[0]

    return router
