import os
import re
import subprocess
import uuid
import shutil
import hashlib
import traceback
import time
import json
import tempfile
import edge_tts
from dotenv import load_dotenv

load_dotenv(override=True)

# --- Auto-Inject MiKTeX into PATH for Manim (Windows Fallback) ---
import shutil
if not shutil.which("latex"):
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    
    potential_paths = []
    if local_app_data:
        potential_paths.extend([
            os.path.join(local_app_data, r"Programs\MiKTeX\miktex\bin\x64"),
            os.path.join(local_app_data, r"Programs\MiKTeX 2.9\miktex\bin\x64"),
        ])
    if program_files:
        potential_paths.extend([
            os.path.join(program_files, r"MiKTeX\miktex\bin\x64"),
            os.path.join(program_files, r"MiKTeX 2.9\miktex\bin\x64"),
        ])
    
    for p in potential_paths:
        if os.path.exists(p):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + p
            break
# ----------------------------------------------

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, status, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
import secrets
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import engine, get_db, Base, SessionLocal
import models

# Create database tables (and the columns later phases added to existing tables: database.ensure_schema)
models.Base.metadata.create_all(bind=engine)
from database import ensure_schema
_schema_added = ensure_schema(engine, {"ai_generation_runs": models.RUN_COLUMN_ADDITIONS}, models.RUN_INDEXES)
if _schema_added:
    print(f"[DB] Added columns: {', '.join(_schema_added)}")

SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# Initialize admin user if not exists
db = SessionLocal()
try:
    admin_user = db.query(models.User).filter(models.User.username == "admin").first()
    if not admin_user:
        hashed_password = get_password_hash("kutty@KONCEPTS$2026")
        admin_user = models.User(username="admin", password_hash=hashed_password)
        db.add(admin_user)
        db.commit()
finally:
    db.close()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        # Scoped tokens (e.g. signed video download links) are not logins
        if username is None or payload.get("scope"):
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

app = FastAPI()

# Lesson video exports: job records, upload, storage and download (exports.py); finished videos are
# also registered in the asset library (register_export_asset, defined with the library below)
from exports import create_exports_router
app.include_router(create_exports_router(get_current_user, SECRET_KEY, ALGORITHM,
                                         register_asset=lambda db, key, export: register_export_asset(db, key, export)))

class UserCreate(BaseModel):
    username: str
    password: str

@app.get("/api/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {"username": current_user.username}

@app.post("/api/register")
def register(user: UserCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can register new users")
    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    hashed_password = get_password_hash(user.password)
    new_user = models.User(username=user.username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created successfully"}

@app.post("/api/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

# --- WebSocket Progress Tracking ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

ws_manager = ConnectionManager()

@app.websocket("/ws/progress")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
# -----------------------------------

# Enable CORS so the browser can make requests to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists to serve videos
STATIC_DIR = os.getenv("STATIC_DIR") or "static_videos"  # a test server can use a folder of its own
os.makedirs(STATIC_DIR, exist_ok=True)
FINAL_VIDEOS_DIR = "final_videos"
os.makedirs(FINAL_VIDEOS_DIR, exist_ok=True)
IMAGES_DIR = "images"
os.makedirs(IMAGES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/final_videos", StaticFiles(directory=FINAL_VIDEOS_DIR), name="final_videos")
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/video_template", StaticFiles(directory="video_template"), name="video_template")

# Asset library: reusable media with stable IDs, stored through storage.py (assets.py)
from assets import build_library, create_assets_router, display_name, register_system_assets
asset_library = build_library(STATIC_DIR, "video_template")
# Finished lesson videos stay where the export stored them (never copied, never deleted by the library)
import exports as _exports
asset_library.volumes["exports"] = _exports.STORAGE
app.include_router(create_assets_router(asset_library, get_current_user, SECRET_KEY, ALGORITHM))

def register_export_asset(db, key, export):
    """A finished export's video file (WebM or its MP4 copy) as the owner's library asset."""
    name = export.file_name if key.endswith("." + (export.format or "webm")) else (export.file_name or "lesson").rsplit(".", 1)[0] + ".mp4"
    return asset_library.adopt(db, "exports", key, owner_id=export.user_id, source="export", file_name=name,
                               details={"export_id": export.id, "title": export.title})
_assets_db = SessionLocal()
try:
    register_system_assets(asset_library, _assets_db)  # Aadhi's clips and the intro media, shared by all users
except Exception as e:
    print(f"[ASSETS] System assets were not registered: {e}")
finally:
    _assets_db.close()

# Visual router: plans where each scene's visual comes from, without generating anything (visuals.py)
from assets import LINK_TTL as ASSET_LINK_TTL
from visuals import ai_generation_enabled, create_visuals_router
# AI media cache: an equivalent AI image or video is generated once and then reused (ai_cache.py)
from access import make_link_token
from ai_cache import AICache, normalize_text

ai_cache = AICache(asset_library)
# Test-only: AI_FAKE_PROVIDER=1 replaces every AI provider with local stand-ins (automated browser
# tests run a separate server with it); the real providers are then not registered at all
AI_FAKE_PROVIDER = os.getenv("AI_FAKE_PROVIDER") == "1"

def current_ai_video_provider():
    """The AI video provider the AI server was started with ("Start AI Server" on the page)."""
    if AI_FAKE_PROVIDER:
        return "fake"
    if ltx_pipeline is None:
        return "offline"
    if isinstance(ltx_pipeline, str):
        return {"GEMINI_API": "veo", "MANUAL": "manual"}.get(ltx_pipeline, ltx_pipeline.lower())
    return "ltx"

# AI media layer (Phase 8): every AI image/video provider behind one interface (ai_providers.py); the
# choice of provider, the cache, retries, fallback, validation and provenance in one place (ai_media.py).
# Phase 9: runs are durable (ai_runs.py) and interrupted ones are continued by the recovery manager (ai_recovery.py)
import ai_providers
import ai_runs
from ai_media import AIMediaService, GenerationFailed
from ai_recovery import RecoveryManager
from visuals import run_request, still_wanted, store_scene_plans
ai_registry = ai_providers.ProviderRegistry(video_mode=lambda: current_ai_video_provider(), ltx_pipeline=lambda: ltx_pipeline,
                                            fake=AI_FAKE_PROVIDER)
ai_media = AIMediaService(asset_library, ai_cache, ai_registry, static_dir=STATIC_DIR, generation_enabled=ai_generation_enabled)
ai_recovery = RecoveryManager(ai_media)

def _asset_link(user):
    return lambda asset: f"/api/assets/{asset.id}/content?token={make_link_token(SECRET_KEY, ALGORITHM, user.username, 'asset', asset.id, ASSET_LINK_TTL)}"

def _lesson_after_generation(db, run):
    """A finished generation for a scene of a saved lesson that has no visual yet is kept in that lesson's plan
    at once, so the lesson has it even if the page that asked was closed (Visual Review decisions still come
    first). A visual the lesson already uses is never replaced, and a New AI Version (forced) is applied by the
    user, not here: the earlier version and its lesson references stay exactly as they were (Phase 5)."""
    if run.slot == "presenter":  # a presenter clip (Phase 12): kept in the scene's presenter plan
        user = db.get(models.User, run.user_id)
        if user is not None:
            _presenters.store_generated_clip(db, run, asset_library, _asset_link(user))
        return
    if run.slot == "background":  # a lesson's AI background (Phase 13): kept for the scenes waiting for it
        if not run.forced:
            _cinematic.store_generated_background(db, run, None)
        return
    request = run_request(run)  # Phase 20: the scene is found by its id when the run recorded one (it may have moved)
    if run.forced or run.project_id is None or (run.scene_index is None and not request.get("scene_id")) or run.slot not in ("main", "side"):
        return
    project = db.query(models.Project).filter(models.Project.id == run.project_id, models.Project.user_id == run.user_id).first()
    user = db.get(models.User, run.user_id)
    if project is None or user is None:
        return
    # fill_slot: kept only while the scene still shows nothing for this slot (checked on the lesson as it is when written)
    store_scene_plans(db, user, asset_library, project, run.scene_index, _asset_link(user), ai_cache, ai_media,
                      scene_id=request.get("scene_id"), fill_slot=run.slot)

ai_media.on_completed = _lesson_after_generation

# AI Presenter / AI Teacher (Phase 12): presenter profiles, the Presenter Director, speech timelines, presenter
# clips through the AI media layer (cache, durable runs, recovery) and presenter decisions in Visual Review
import presenters as _presenters

def _still_wanted(db, run):
    return _presenters.presenter_still_wanted(db, run) if run.slot == "presenter" else still_wanted(db, run)

ai_media.is_wanted = _still_wanted

# Secure Manim rendering (Phase 10): AI-generated Manim code is untrusted and runs only in an isolated sandbox
# (manim_sandbox.py), as durable jobs on the same runs, leases and recovery as AI media (manim_jobs.py)
import manim_security
import visuals as _visuals
from manim_jobs import ManimFailed, ManimService
from manim_sandbox import ManimSandbox
manim_sandbox = ManimSandbox()
manim_service = ManimService(asset_library, ai_cache, manim_sandbox, static_dir=STATIC_DIR)
manim_service.on_completed = _lesson_after_generation
manim_service.is_wanted = still_wanted  # Visual Review decisions first; a changed scene code is not rendered
ai_recovery.register("manim", manim_service)
_visuals.use_manim_renders(manim_service)  # the visual router reuses animations rendered before

async def _presenter_tts(text, voice, engine, user):
    """The lesson's own TTS (the same files /generate-audio makes), for the presenter's speech timeline."""
    return await _generate_audio(AudioRequest(text=text, voice=voice, tts_engine=engine), user)

def _static_path(url):
    return os.path.join(STATIC_DIR, url.split("?", 1)[0].rsplit("/", 1)[-1])

async def _presenter_answer(db, user, request, wait):
    try:
        if not wait:
            outcome, run = ai_media.start_background(db, user, request)
            if run is not None:
                return JSONResponse(status_code=202, content={"status": run.status, "run_id": run.id, "cache_hit": False, "generated": False})
        else:
            outcome = await ai_media.generate(db, user, request)
    except GenerationFailed as failure:
        _refuse(failure)
    return _video_answer(outcome, user)

app.include_router(_presenters.create_presenters_router(
    get_current_user=get_current_user, registry=ai_registry, ai_media=ai_media, library=asset_library, tts=_presenter_tts,
    path_of=_static_path, link_for=_asset_link, answer_for=_presenter_answer))

# Cinematic scene composition (Phase 13): the composer (layers, safe areas, camera, timeline, transitions) and its
# place in Visual Review; an optional AI background goes through the same AI media layer (cache, runs, recovery)
import cinematic as _cinematic

async def _cinematic_answer(db, user, request, wait):
    try:
        if not wait:
            outcome, run = ai_media.start_background(db, user, request)
            if run is not None:
                return JSONResponse(status_code=202, content={"status": run.status, "run_id": run.id, "cache_hit": False, "generated": False})
        else:
            outcome = await ai_media.generate(db, user, request)
    except GenerationFailed as failure:
        _refuse(failure)
    return _image_result(outcome.asset, user, cache_hit=outcome.cache_hit, generated=outcome.generated, **outcome.info())

app.include_router(_cinematic.create_cinematic_router(get_current_user=get_current_user, library=asset_library,
                                                      link_for=_asset_link, answer_for=_cinematic_answer))

# Quality & Consistency Engine (Phase 18): a lesson's quality report, derived on demand from its scenes and plans (read-only:
# nothing is saved, generated or approved)
import quality as _quality
app.include_router(_quality.create_quality_router(get_current_user=get_current_user, library=asset_library, link_for=_asset_link))

# Advanced Video Editor (Phase 19): an edited lesson saved in place with a revision check (the same history entry)
import editor_api as _editor_api
app.include_router(_editor_api.create_editor_router(get_current_user=get_current_user, library=asset_library))

# Source Document Formatting Assistant (Phase 11): analyses an uploaded document (structure, content, quality,
# Aadhi-ready structure) before the existing lesson generation; an AI analysis is a durable run like the others
from source_documents import DocumentService, create_source_documents_router
document_service = DocumentService()
ai_recovery.register("document_analysis", document_service)
app.include_router(create_source_documents_router(document_service, get_current_user))

# End-to-End Studio (Phase 20): its lesson writing service and routes are set up with the lesson save (save_lesson, below)
import studio as _studio

def _sweep_manim_workspaces():
    db = SessionLocal()
    try:
        manim_service.sweep(db)
    except Exception as e:  # noqa: BLE001 - cleanup must never stop the server
        print(f"[MANIM] workspace sweep failed: {e}")
    finally:
        db.close()

def current_ai_image_provider():
    return ai_registry.preferred("image")

app.include_router(create_visuals_router(asset_library, get_current_user, SECRET_KEY, ALGORITHM, ASSET_LINK_TTL,
                                         ai_cache=ai_cache, ai_media=ai_media))

@app.on_event("startup")
async def _start_recovery():
    """Continues interrupted AI generations and export finishing in the background; serving starts at once."""
    asyncio.get_running_loop().run_in_executor(None, _sweep_manim_workspaces)  # sandbox workspaces a crash left
    if ai_recovery.enabled():
        app.state.recovery_task = asyncio.create_task(ai_recovery.run_forever())
        asyncio.get_running_loop().run_in_executor(
            None, lambda: _exports.recover_exports(lambda db, key, export: register_export_asset(db, key, export)))

@app.on_event("shutdown")
async def _stop_recovery():
    """A stopping server gives up its runs at once (their leases expire now), so the next start continues them."""
    task = getattr(app.state, "recovery_task", None)
    if task:
        task.cancel()
    for run_task in (list(ai_media.tasks.values()) + list(manim_service.tasks.values()) + list(document_service.tasks.values())
                     + list(lesson_script_service.tasks.values())):  # presenter clips run in ai_media
        run_task.cancel()  # a sandboxed render is stopped (its whole process tree) before its worker gives up

@app.get("/api/ai-cache/stats")
def ai_cache_stats(current_user: models.User = Depends(get_current_user)):
    """Development view of the AI media cache and providers: hits, misses, generations (counts only)."""
    media = ai_media.snapshot()
    return {**ai_cache.snapshot(), "provider_calls": media["provider_calls"],
            "provider_calls_by_provider": media["provider_calls_by_provider"], "media": media["media"],
            "video_provider": current_ai_video_provider(), "image_provider": current_ai_image_provider()}

@app.get("/api/ai-media/providers")
def ai_media_providers(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The AI providers of this server for the settings panel: state (available / not configured /
    disabled / temporarily unavailable) from configuration and recent failures only (no provider is
    called), capabilities, preference and fallback order, and the user's generations in progress,
    recovering or needing attention. Never a secret."""
    return {**ai_registry.status(), "ai_generation_enabled": ai_generation_enabled(), "video_mode": current_ai_video_provider(),
            "generations": ai_recovery.summary(db, f"user:{current_user.id}")}

def _iso(value):
    return value.isoformat() + "Z" if value else None

STATE_LABELS = {"queued": "Queued", "running": "Generating", "recovering": "Recovering", "cancel_requested": "Cancelling",
                "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled", "needs_attention": "Needs attention"}

def _explanation(run, attempts):
    """In words: what happened to a run, especially after an interruption."""
    recovered = [a for a in attempts if a.recovered]
    if run.status == "needs_attention":
        return run.error_message
    if run.status == "recovering":
        return "The server was interrupted while this was being generated; Aadhi is picking it up again."
    if run.status == "queued" and run.not_before:
        return "Waiting to try again after a temporary provider problem."
    if run.status == "queued" and run.batch_id:
        return "Waiting its turn in the lesson's background generation."
    if run.status == "completed" and recovered:
        how = json.loads(recovered[-1].detail or "{}").get("recovered_how")
        if how == "resumed_job":
            return "Recovered after an interruption: the provider's job was picked up again, no new request was sent."
        if how == "retried_free":
            return "Recovered after an interruption: the provider had not kept the request, so it was sent again (no cost)."
        if how == "rendered_again":
            return "Recovered after an interruption: the animation was rendered again from the start in the sandbox."
        return "Recovered after an interruption: the finished result was saved, nothing was generated twice."
    if run.status == "completed" and run.cache_hit:
        return "Reused an earlier result: nothing new was generated."
    if run.status in ("failed", "cancelled"):
        return run.error_message
    return None

def _run_view(db, run, user, with_attempts=True):
    detail = json.loads(run.detail or "{}")
    request = json.loads(run.request or "{}")
    attempts = ai_runs.attempts_of(db, run.id) if with_attempts else []
    kind = run.kind or "ai_media"
    label = "Rendering" if kind == "manim" and run.status == "running" else STATE_LABELS.get(run.status, run.status)
    data = {"run_id": run.id, "kind": kind, "status": run.status, "state_label": label,
            "profile": request.get("profile") if kind == "manim" else None, "media_type": run.media_type, "requested_provider": run.requested_provider,
            "provider": run.provider, "model": run.model, "fallback_from": run.fallback_from, "attempts": run.attempts,
            "cache_hit": bool(run.cache_hit), "forced": bool(run.forced), "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "last_checked_at": _iso(run.last_checked_at),
            "background": bool(detail.get("background")), "project_id": run.project_id, "scene_index": run.scene_index,
            "slot": run.slot, "batch_id": run.batch_id, "recovery_count": run.recovery_count or 0,
            "prompt": request.get("prompt") if kind != "manim" else None,  # a render's code stays in the lesson
            "provider_job_id": run.provider_job_id,
            "tried": [{k: a.get(k) for k in ("provider", "attempt", "category", "ms")} for a in detail.get("attempts", [])],
            "history": [ai_runs.attempt_view(a) for a in attempts],
            "explanation": _explanation(run, attempts),
            "actions": (["cancel"] if run.status in ("queued", "running", "recovering") else [])
            + (["retry", "dismiss"] if run.status == "needs_attention" else [])}
    if run.error_category:
        data["error"] = {"category": run.error_category, "message": run.error_message, "http_status": run.http_status}
    if run.started_at and run.finished_at:
        data["generation_ms"] = int((run.finished_at - run.started_at).total_seconds() * 1000)
    if run.status == "completed" and run.asset_id:
        asset = db.get(models.Asset, run.asset_id)
        if asset is not None and asset.status == "ready":
            extra = {k: v for k, v in (("provider", run.provider), ("model", run.model), ("fallback_from", run.fallback_from),
                                       ("run_id", run.id), ("warnings", detail.get("warnings") or None)) if v is not None}
            shape = _video_result if run.media_type in ("video", "presenter") else _image_result
            data["result"] = shape(asset, user, cache_hit=bool(run.cache_hit), generated=not run.cache_hit, **extra)
    return data

def _own_run(db, run_id, user):
    run = db.get(models.AIGenerationRun, run_id)
    if run is None or run.scope_key != f"user:{user.id}":
        raise HTTPException(status_code=404, detail="Generation not found.")
    return run

@app.get("/api/ai-media/runs")
def ai_media_runs(limit: int = 20, project_id: int | None = None, active: bool = False, batch_id: str | None = None,
                  current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The user's generations (newest first): all recent ones, or those of one lesson, in progress (active:
    queued / running / recovering / cancelling, and needing attention) or of one lesson batch."""
    query = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.scope_key == f"user:{current_user.id}")
    if project_id is not None:
        query = query.filter(models.AIGenerationRun.project_id == project_id)
    if active:
        query = query.filter(models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE) + ["needs_attention"]))
    if batch_id:
        query = query.filter(models.AIGenerationRun.batch_id == batch_id)
    runs = query.order_by(models.AIGenerationRun.created_at.desc()).limit(max(1, min(limit, 200))).all()
    return {"runs": [_run_view(db, run, current_user, with_attempts=False) for run in runs]}

@app.get("/api/ai-media/runs/{run_id}")
def ai_media_run(run_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One generation, e.g. a background one the page is waiting for; `result` has the media once it is done,
    `history` every attempt (provider, job, state, recovery)."""
    run = _own_run(db, run_id, current_user)
    if ai_recovery.executor_for(run) is ai_media:
        run = ai_media.check_interrupted(db, run)
    return _run_view(db, run, current_user)

@app.post("/api/ai-media/runs/{run_id}/cancel")
async def ai_media_cancel(run_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cancels a generation: at once while queued; otherwise its worker (here, in another process, or recovery if
    that worker died) stops it and cancels the provider's job where the provider allows it."""
    run = _own_run(db, run_id, current_user)
    if run.status not in ("queued", "running", "recovering"):
        raise HTTPException(status_code=409, detail="This generation has already finished." if run.status in ai_runs.TERMINAL
                            else "This generation is already being cancelled.")
    executor = ai_recovery.executor_for(run)  # AI media, or a sandboxed Manim render
    answer = executor.request_cancel(db, run)
    if answer is None:
        raise HTTPException(status_code=409, detail="This generation changed state meanwhile; refresh and try again.")
    task = executor.tasks.get(run.id)
    if answer == "cancelling" and task is not None:
        await asyncio.wait({task}, timeout=5)  # a worker of this process stops within moments; others follow via the run
    return JSONResponse(status_code=202 if answer == "cancelling" else 200, content={"status": answer, "run_id": run.id})

class RunDecision(BaseModel):
    action: str  # retry | dismiss

@app.post("/api/ai-media/runs/{run_id}/resolve")
def ai_media_resolve(run_id: str, body: RunDecision, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A person's decision about a generation that needs attention: retry it (accepting that the provider might
    already have made — and billed — it) or dismiss it."""
    run = _own_run(db, run_id, current_user)
    if body.action not in ("retry", "dismiss"):
        raise HTTPException(status_code=422, detail="action must be retry or dismiss.")
    if run.status != "needs_attention" or not ai_media.resolve_attention(db, run, body.action):
        raise HTTPException(status_code=409, detail="This generation does not need attention.")
    db.expire_all()
    return _run_view(db, db.get(models.AIGenerationRun, run.id), current_user)

class LessonBatchRequest(BaseModel):
    media: str = "video"  # video | image | all

def _scene_hidden(scenes, index):
    """Phase 19: a scene hidden in the editor is not played or exported, so nothing is generated for it."""
    scene = scenes[index] if isinstance(index, int) and 0 <= index < len(scenes) else None
    edit = scene.get("edit") if isinstance(scene, dict) else None
    return isinstance(edit, dict) and edit.get("hidden") is True


def _scene_id_at(scenes, index):
    """Phase 20: the scene's own id (recorded in its generation's request, so the result finds the scene if it moved)."""
    scene = scenes[index] if isinstance(index, int) and 0 <= index < len(scenes) else None
    scene_id = scene.get("scene_id") if isinstance(scene, dict) else None
    return scene_id if isinstance(scene_id, str) and _editor_api.SCENE_ID.fullmatch(scene_id) else None


@app.post("/api/ai-media/lessons/{project_id}/generate")
def ai_media_lesson_batch(project_id: int, body: LessonBatchRequest, current_user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Queues every AI visual a saved lesson still needs (its visual plan, Visual Review decisions and the cache
    first) to be generated on the server, one by one: it continues if the page is closed and after a restart,
    and scenes already made are never made again."""
    from visuals import PlanOptions, plan_lesson, requests_from_scenes
    if body.media not in ("video", "image", "all"):
        raise HTTPException(status_code=422, detail="media must be video, image or all.")
    project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    if not ai_generation_enabled():
        raise HTTPException(status_code=403, detail="AI generation is turned off on this server.")
    scenes = json.loads(project.json_data or "{}").get("scenes") or []
    wanted = {"video": ("AI_VIDEO",), "image": ("AI_IMAGE",), "all": ("AI_VIDEO", "AI_IMAGE")}[body.media]
    prompts = {(r.scene_index, r.slot): r.generation_prompt for r in requests_from_scenes(scenes)}
    plans = plan_lesson(db, current_user, asset_library, scenes, PlanOptions(), _asset_link(current_user), ai_cache, ai_media)
    requests = [ai_providers.MediaRequest(media_type="video" if p.source.value == "AI_VIDEO" else "image",
                                          prompt=prompts[(p.scene_index, p.slot)], project_id=project_id, scene_index=p.scene_index,
                                          slot=p.slot, purpose="lesson-batch", scene_id=_scene_id_at(scenes, p.scene_index))
                for p in plans if p.requires_generation and p.source.value in wanted and p.provider != "manual"
                and prompts.get((p.scene_index, p.slot)) and not _scene_hidden(scenes, p.scene_index)]
    batch_id, runs = ai_media.start_batch(db, current_user, requests)
    media_of = {"AI_VIDEO": "VIDEO", "AI_IMAGE": "STATIC_IMAGE"}
    return {"batch_id": batch_id, "runs": [_run_view(db, r, current_user, with_attempts=False) for r in runs],
            "already_available": sum(1 for p in plans if (p.scene_index, p.slot) in prompts and not p.requires_generation
                                     and p.asset_id and p.media.value in {media_of[w] for w in wanted})}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/app.js", response_class=FileResponse)
async def get_app_js():
    if os.path.exists("app.js"):
        return FileResponse("app.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="app.js not found")

@app.get("/mascot.js", response_class=FileResponse)
async def get_mascot_js():
    if os.path.exists("mascot.js"):
        return FileResponse("mascot.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="mascot.js not found")

@app.get("/export.js", response_class=FileResponse)
async def get_export_js():
    if os.path.exists("export.js"):
        return FileResponse("export.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="export.js not found")

@app.get("/assets.js", response_class=FileResponse)
async def get_assets_js():
    if os.path.exists("assets.js"):
        return FileResponse("assets.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="assets.js not found")

@app.get("/visuals.js", response_class=FileResponse)
async def get_visuals_js():
    if os.path.exists("visuals.js"):
        return FileResponse("visuals.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="visuals.js not found")

@app.get("/review.js", response_class=FileResponse)
async def get_review_js():
    if os.path.exists("review.js"):
        return FileResponse("review.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="review.js not found")

# Advanced Video Editor (Phase 19): its model, its workspace and its styles (fixed file names, nothing else served)
@app.get("/editor.js", response_class=FileResponse)
async def get_editor_js():
    if os.path.exists("editor.js"):
        return FileResponse("editor.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="editor.js not found")

@app.get("/editor_ui.js", response_class=FileResponse)
async def get_editor_ui_js():
    if os.path.exists("editor_ui.js"):
        return FileResponse("editor_ui.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="editor_ui.js not found")

@app.get("/editor.css", response_class=FileResponse)
async def get_editor_css():
    if os.path.exists("editor.css"):
        return FileResponse("editor.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="editor.css not found")

@app.get("/studio.js", response_class=FileResponse)
async def get_studio_js():
    if os.path.exists("studio.js"):
        return FileResponse("studio.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="studio.js not found")

@app.get("/stage-fit.css", response_class=FileResponse)
async def get_stage_fit_css():
    if os.path.exists("stage-fit.css"):
        return FileResponse("stage-fit.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="stage-fit.css not found")

@app.get("/product.css", response_class=FileResponse)
async def get_product_css():
    if os.path.exists("product.css"):
        return FileResponse("product.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="product.css not found")

@app.get("/ui.css", response_class=FileResponse)
async def get_ui_css():
    if os.path.exists("ui.css"):
        return FileResponse("ui.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="ui.css not found")

@app.get("/studio.css", response_class=FileResponse)
async def get_studio_css():
    if os.path.exists("studio.css"):
        return FileResponse("studio.css", media_type="text/css")
    raise HTTPException(status_code=404, detail="studio.css not found")

@app.get("/cinematic.js", response_class=FileResponse)
async def get_cinematic_js():
    if os.path.exists("cinematic.js"):
        return FileResponse("cinematic.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="cinematic.js not found")

@app.get("/presenter.js", response_class=FileResponse)
async def get_presenter_js():
    if os.path.exists("presenter.js"):
        return FileResponse("presenter.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="presenter.js not found")

@app.get("/sources.js", response_class=FileResponse)
async def get_sources_js():
    if os.path.exists("sources.js"):
        return FileResponse("sources.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="sources.js not found")

import urllib.request
import urllib.parse

@app.get("/get-image")
async def get_image(prompt: str, subjectName: str = ""):
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
        
    # Create a clean cache key
    cache_key = hashlib.md5(prompt.encode('utf-8')).hexdigest()
    image_path = os.path.join(IMAGES_DIR, f"{cache_key}.jpg")
    
    if os.path.exists(image_path):
        return FileResponse(image_path, media_type="image/jpeg")

    # Generating a new image calls an AI provider: honour the server-wide switch
    if not ai_generation_enabled():
        raise HTTPException(status_code=403, detail="AI image generation is turned off on this server.")

    # Doesn't exist, download it
    encoded_prompt = urllib.parse.quote(prompt)
    seed = int(time.time() * 1000) % 100000
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=1200&nologo=true&seed={seed}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response, open(image_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        return FileResponse(image_path, media_type="image/jpeg")
    except Exception as e:
        print(f"Failed to fetch image: {e}")
        # Try fallback
        try:
            fallback = urllib.parse.quote(f"{subjectName} abstract aesthetic background")
            fallback_url = f"https://image.pollinations.ai/prompt/{fallback}?width=800&height=1200&seed={seed}"
            req_fb = urllib.request.Request(fallback_url, headers=headers)
            with urllib.request.urlopen(req_fb) as response, open(image_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            return FileResponse(image_path, media_type="image/jpeg")
        except:
            raise HTTPException(status_code=500, detail="Failed to generate image")

@app.get("/get-gif")
async def get_gif(query: str, randomize: bool = False):
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")
        
    api_key = os.environ.get("GIPHY_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GIPHY_API_KEY is not set in .env")
        
    encoded_query = urllib.parse.quote(query)
    import random
    url = f"https://api.giphy.com/v1/gifs/search?api_key={api_key}&q={encoded_query}&limit=10&rating=g"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if data and data.get("data") and len(data["data"]) > 0:
                if randomize:
                    gif_obj = random.choice(data["data"])
                else:
                    gif_obj = data["data"][0]
                gif_url = gif_obj["images"]["original"]["url"]
                return {"status": "success", "gif_url": gif_url}
            else:
                raise HTTPException(status_code=404, detail="No GIFs found for query")
    except Exception as e:
        print(f"Failed to fetch GIF: {e}")
        raise HTTPException(status_code=500, detail=str(e))
class RegenerateManimRequest(BaseModel):
    slide_title: str
    narration: str
    existing_code: str
    user_feedback: str | None = None

@app.post("/regenerate-manim")
async def regenerate_manim(request: RegenerateManimRequest, current_user: models.User = Depends(get_current_user)):
    from google import genai
    from google.genai import types
    
    feedback_section = f"\n\nCRITICAL USER FEEDBACK - YOU MUST FOLLOW THIS:\n{request.user_feedback}\n" if request.user_feedback else ""
    
    prompt = f"""You are an expert Python Manim animator.
The user wants to REGENERATE the Manim code for the following presentation slide because the previous animation was inaccurate or had layout issues.
{feedback_section}

Slide Title: {request.slide_title}
Narration (Context): {request.narration}

Here is the OLD code that was generated:
```python
{request.existing_code}
```

Please generate a BRAND NEW, completely rewritten Manim script for this scene.
RULES:
1. ONLY output raw Python code. Do not wrap in markdown or backticks.
2. OVERLAP PREVENTION: You MUST use `VGroup(*elements).arrange(DOWN, aligned_edge=LEFT)` or explicitly set coordinates to completely prevent overlaps.
3. PREFER `MovingCameraScene`: Instead of a static `Scene`, use `MovingCameraScene` and animate `self.camera.frame.animate.move_to(obj)` to dynamically pan and zoom to important elements.
4. USE MORPHING: For mathematical formulas, strictly use `TransformMatchingTex` instead of `Transform` or `Write` to visually morph equations seamlessly.
5. HIGHLIGHTING: To emphasize concepts during the narration, use `SurroundingRectangle(element, color=YELLOW, buff=0.1)` and animate its creation.
6. Keep it highly cinematic and directly synchronized to the Narration context.
"""
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.4)
        )
        
        new_code = response.text.strip()
        if new_code.startswith("```python"):
            new_code = new_code[9:]
        if new_code.endswith("```"):
            new_code = new_code[:-3]
            
        return {"status": "success", "manim_code": new_code.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenderRequest(BaseModel):
    code: str
    profile: str | None = None      # preview | standard (default) | high_quality
    project_id: int | None = None   # the saved lesson and scene slot the animation is for (optional)
    scene_index: int | None = None
    slot: str | None = None         # main | side
    force: bool = False             # render again although exactly this animation was rendered before
    wait: bool = True               # False: 202 with the run at once; follow it at /api/ai-media/runs/{run_id}
    scene_id: str | None = None     # Phase 20: the scene's id (s- + 12 hex), so the render finds its scene if it moved

MANIM_HEAL_ATTEMPTS = max(0, int(os.getenv("MANIM_AUTO_HEAL_ATTEMPTS") or 2))

def _manim_refusal(failure):
    """An animation that could not be rendered: a friendly message and its category (never a host path or secret)."""
    category = failure.category or "sandbox_error"
    return JSONResponse(status_code=failure.status, content={"status": "error", "detail": failure.message, "category": category},
                        headers={"X-Error-Category": category})

def _can_heal():
    """Gemini may rewrite code that crashed while rendering (as before Phase 10), only where AI generation is on
    and a key is configured; the rewritten code goes through the same checks and sandbox."""
    return (MANIM_HEAL_ATTEMPTS > 0 and not AI_FAKE_PROVIDER and ai_generation_enabled()
            and bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")))

def _heal_manim_code(code, error_log):
    from google import genai
    from google.genai import types
    fix_prompt = f"""You are a Python Manim expert.
The following Manim code failed to render.
Here is the error log:
{error_log[-4000:]}

Here is the original code:
```python
{code}
```
Please fix the code. Ensure there are no overlapping elements, use .arrange(DOWN, aligned_edge=LEFT), and fix the exact error mentioned in the log.
Only use the manim, math, numpy and random modules; LaTeX (Tex, MathTex) is not available, use Text instead.
Output ONLY the raw valid Python code, properly escaped, with NO markdown formatting, NO backticks, and NO explanations. Just the raw code.
"""
    try:
        response = genai.Client().models.generate_content(model='gemini-2.5-flash', contents=fix_prompt,
                                                          config=types.GenerateContentConfig(temperature=0.1))
    except Exception as e:  # noqa: BLE001 - healing is an extra; the render's own error is reported
        print(f"[MANIM] auto-heal unavailable: {e}")
        return None
    new_code = (response.text or "").strip()
    if new_code.startswith("```python"):
        new_code = new_code[9:]
    if new_code.endswith("```"):
        new_code = new_code[:-3]
    return new_code.strip() or None

@app.post("/render")
async def render_manim(request: RenderRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Renders AI-generated Manim code. The code is untrusted: it runs only in the secure sandbox
    (manim_sandbox.py: isolated files, no network, no secrets, resource limits), never in this process, as a
    durable job (a Phase 9 run of kind "manim", recovered after a crash) whose video becomes a library asset,
    cached by code + Manim version + render profile. Without a secure runtime the answer is 503 "Secure Manim
    execution is unavailable in this deployment." -- there is no unsafe fallback."""
    if request.slot is not None and request.slot not in ("main", "side"):
        raise HTTPException(status_code=422, detail="slot must be main or side.")
    _own_lesson(db, current_user, request.project_id)  # Phase 20: only the user's own lesson
    code, healed = request.code or "", 0
    while True:
        try:
            result = await manim_service.render(db, current_user, code, profile=request.profile, project_id=request.project_id,
                                                scene_index=request.scene_index, slot=request.slot, force=request.force,
                                                wait=request.wait, scene_id=_scene_id_of(request))
            break
        except ManimFailed as failure:
            if failure.category == "render_failed" and healed < MANIM_HEAL_ATTEMPTS and _can_heal():
                print(f"[MANIM] render failed; asking Gemini to fix the code (attempt {healed + 1}/{MANIM_HEAL_ATTEMPTS})")
                fixed = await asyncio.to_thread(_heal_manim_code, code, failure.detail or failure.message)
                if fixed and fixed != code:
                    code, healed = fixed, healed + 1
                    continue
            return _manim_refusal(failure)
    outcome, run = result if not request.wait else (result, None)
    if outcome is None:
        return JSONResponse(status_code=202, content={"status": "queued", "run_id": run.id,
                                                      "detail": "The animation is rendering; follow it at /api/ai-media/runs/" + run.id})
    answer = _video_result(outcome.asset, current_user, cache_hit=outcome.cache_hit, generated=outcome.generated,
                           provider="manim", model=outcome.model, **({"run_id": outcome.run_id} if outcome.run_id else {}))
    if healed:
        answer.update(healed=healed, manim_code=code)
    return answer

@app.get("/api/manim/sandbox")
async def manim_sandbox_status(check: bool = False, current_user: models.User = Depends(get_current_user)):
    """Whether animations can be rendered securely on this server, and how: the runtime and its isolation,
    the render profiles and limits, and the health check (a tiny isolated process that must start and be
    refused the application's files and the network; `check=true` runs it again). No host path, no secret."""
    status = manim_sandbox.status()
    if status["available"] and (check or manim_sandbox.health is None):
        await asyncio.to_thread(manim_sandbox.run_health_check)
        status = manim_sandbox.status()
    health = status.get("health") or {}
    status["health"] = {k: health[k] for k in ("ok", "checked", "seconds", "at") if k in health} or None
    status.pop("reason", None)  # may name host paths; the message says what matters
    return {**status, "manim_version": manim_security.MANIM_VERSION, "default_profile": manim_security.DEFAULT_PROFILE,
            "profiles": {name: manim_security.limits(name) for name in manim_security.PROFILES},
            "concurrency": {"global": max(1, int(os.getenv("MANIM_MAX_CONCURRENT") or 1)),
                            "per_user": max(1, int(os.getenv("MANIM_MAX_PER_USER") or 2)), "rendering": len(manim_service.active)},
            "stats": dict(manim_service.stats)}

# --- AI Video Generation Endpoint ---
ltx_pipeline = "MANUAL"

class HardwareRequest(BaseModel):
    profile: str

gemini_api_key = None

@app.post("/start-ai-server")
def start_ai_server(request: HardwareRequest, current_user: models.User = Depends(get_current_user)):
    global ltx_pipeline
    profile = request.profile
    
    if ltx_pipeline is not None:
        return {"status": "success", "message": "AI Server is already running!"}
        
    print(f"\n[INIT] Starting AI Server with Hardware Profile: {profile}")
    try:
        if profile == "rtx_3090_2x":
            from diffusers import LTXPipeline
            import torch
            ltx_pipeline = LTXPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16,
                device_map="balanced"
            )
            try:
                ltx_pipeline.enable_vae_slicing()
            except: pass
            print("Model loaded with Multi-GPU distribution (device_map='balanced') for 2x RTX 3090!")
            
        elif profile == "rtx_4060_laptop":
            from diffusers import LTXPipeline
            import torch
            ltx_pipeline = LTXPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16
            )
            ltx_pipeline.to("cuda")
            try:
                # Critical optimizations for 8GB VRAM
                ltx_pipeline.enable_model_cpu_offload()
                ltx_pipeline.enable_vae_tiling()
                ltx_pipeline.enable_vae_slicing()
            except: pass
            print("Model loaded with CPU offloading and VAE tiling for 8GB VRAM RTX 4060 Laptop!")
            
        elif profile == "gemini_api_video":
            if not os.getenv("GEMINI_API_KEY"):
                raise Exception("GEMINI_API_KEY environment variable is not set. Please add it to your .env file.")
            ltx_pipeline = "GEMINI_API"
            print("Gemini API configured for Veo video generation!")
            
        elif profile == "no_ai_generation":
            ltx_pipeline = "MANUAL"
            print("Server started without AI video generation capabilities.")
            
        else:
            raise HTTPException(status_code=400, detail="Invalid hardware profile.")
            
        return {"status": "success", "message": "AI Server successfully initialized."}
    except Exception as e:
        traceback.print_exc()
        error_msg = str(e)
        if isinstance(e, ImportError):
            error_msg = "Missing required AI libraries (e.g. diffusers, torch). Please install them, or select 'Option 3: No AI Generation'."
        raise HTTPException(status_code=500, detail=error_msg)

class AIVideoRequest(BaseModel):
    prompt: str
    force_regenerate: bool = False  # skip the cache and make a new version; the old asset is kept
    provider: str | None = None     # an explicit provider: honoured, never silently replaced ...
    allow_fallback: bool | None = None  # ... unless this allows other providers (automatic choices fall back by default)
    model: str | None = None
    aspect_ratio: str | None = None     # "16:9", "9:16", ...
    duration_seconds: float | None = None
    wait: bool = True               # false: answer at once and generate in the background (follow run_id)
    project_id: int | None = None   # where the media is for (provenance only); the user's own lesson (Phase 20)
    scene_index: int | None = None
    slot: str | None = None
    scene_id: str | None = None     # Phase 20: the scene's id (s- + 12 hex), so the result finds its scene if it moved

class AIImageRequest(BaseModel):
    prompt: str
    subject_name: str = ""  # only for the fallback image when every provider fails
    force_regenerate: bool = False
    provider: str | None = None
    allow_fallback: bool | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    project_id: int | None = None
    scene_index: int | None = None
    slot: str | None = None
    scene_id: str | None = None

def _media_url(asset, user):
    """Where the page loads an asset: its public /static URL when it lives there (as generated videos
    always have), otherwise a signed content link."""
    if asset.storage_volume == "static":
        return f"/static/{asset.storage_key}"
    token = make_link_token(SECRET_KEY, ALGORITHM, user.username, "asset", asset.id, ASSET_LINK_TTL)
    return f"/api/assets/{asset.id}/content?token={token}"

def _video_result(asset, user, *, cache_hit, generated, **extra):
    # "cached" is the field older pages read; cache_hit / generated are the Phase 5 names
    return {"status": "success", "video_url": _media_url(asset, user), "asset_id": asset.id,
            "cache_hit": cache_hit, "generated": generated, "cached": cache_hit, **extra}

def _image_result(asset, user, *, cache_hit, generated, **extra):
    url = _media_url(asset, user)
    return {"status": "success", "url": url, "image_url": url, "asset_id": asset.id,
            "cache_hit": cache_hit, "generated": generated, **extra}

def _media_request(media_type, body):
    """The provider-independent request (ai_providers.MediaRequest) for a generator call."""
    if body.provider is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", body.provider):
        raise HTTPException(status_code=422, detail="provider must be a provider name.")
    if body.model is not None and not re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", body.model):
        raise HTTPException(status_code=422, detail="model must be a model code.")
    if body.aspect_ratio is not None and not re.fullmatch(r"[1-9][0-9]?:[1-9][0-9]?", body.aspect_ratio):
        raise HTTPException(status_code=422, detail="aspect_ratio must look like 16:9.")
    duration = getattr(body, "duration_seconds", None)
    if duration is not None and not 0 < duration <= 120:
        raise HTTPException(status_code=422, detail="duration_seconds must be between 0 and 120.")
    if body.slot is not None and body.slot not in ("main", "side"):
        raise HTTPException(status_code=422, detail="slot must be main or side.")
    return ai_providers.MediaRequest(media_type=media_type, prompt=body.prompt, provider=body.provider,
                                     allow_fallback=body.allow_fallback, model=body.model, aspect_ratio=body.aspect_ratio,
                                     duration_seconds=duration, force_regenerate=body.force_regenerate,
                                     project_id=body.project_id, scene_index=body.scene_index, slot=body.slot,
                                     scene_id=_scene_id_of(body))

def _scene_id_of(body):
    """Phase 20: the scene id a generation request names (s- + 12 hex), else None (anything else is ignored)."""
    scene_id = getattr(body, "scene_id", None)
    return scene_id if isinstance(scene_id, str) and _editor_api.SCENE_ID.fullmatch(scene_id) else None

def _own_lesson(db, user, project_id):
    """Phase 20: a generation or render may name a lesson only if it is the user's own (404 otherwise, the same answer
    as for a lesson that does not exist), so nothing is ever attached to another user's lesson."""
    if project_id is not None and not db.query(models.Project.id).filter(models.Project.id == project_id,
                                                                         models.Project.user_id == user.id).first():
        raise HTTPException(status_code=404, detail="Lesson not found.")

def _refuse(failure):
    headers = {"Retry-After": str(max(1, int(failure.retry_after + 0.999)))} if failure.retry_after else None
    raise HTTPException(status_code=failure.status, detail=failure.message, headers=headers)

def _video_answer(outcome, user):
    if outcome.manual and outcome.manual.get("status") == "manual_required":
        return {**outcome.manual, "cache_hit": False, "generated": False, "provider": "manual"}
    extra = outcome.info()
    if outcome.manual:
        extra["manual"] = True
    return _video_result(outcome.asset, user, cache_hit=outcome.cache_hit, generated=outcome.generated, **extra)

@app.post("/generate-ai-video")
async def generate_ai_video(request: AIVideoRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """An AI video for a prompt: the cached one when this request was made before (no AI call, even with
    AI generation switched off), otherwise generated once by the chosen provider (falling back to another
    when allowed) and registered in the library. wait=false returns a run to follow instead of waiting."""
    if not normalize_text(request.prompt):
        raise HTTPException(status_code=400, detail="A prompt is required.")
    media_request = _media_request("video", request)
    _own_lesson(db, current_user, request.project_id)
    try:
        if not request.wait:
            outcome, run = ai_media.start_background(db, current_user, media_request)
            if run is not None:
                return JSONResponse(status_code=202, content={"status": run.status, "run_id": run.id,
                                                              "cache_hit": False, "generated": False})
        else:
            outcome = await ai_media.generate(db, current_user, media_request)
    except GenerationFailed as failure:
        _refuse(failure)
    return _video_answer(outcome, current_user)

@app.post("/generate-ai-image")
async def generate_ai_image(request: AIImageRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """An AI image for a prompt, through the same layer and cache as AI videos, registered in the library.
    (GET /get-image stays for older pages: unauthenticated, cached by prompt only, not registered.)"""
    if not normalize_text(request.prompt):
        raise HTTPException(status_code=400, detail="A prompt is required.")
    media_request = _media_request("image", request)
    _own_lesson(db, current_user, request.project_id)
    try:
        outcome = await ai_media.generate(db, current_user, media_request)
        return _image_result(outcome.asset, current_user, cache_hit=outcome.cache_hit, generated=outcome.generated, **outcome.info())
    except GenerationFailed as failure:
        # Only a provider failure falls back to a subject image; switched off / not set up stays an error
        if not failure.errors or not normalize_text(request.subject_name):
            _refuse(failure)
        print(f"Failed to generate image: {failure.message}")
    # The fallback is cached under its own request, never as the image that was asked for
    fallback = ai_providers.MediaRequest(media_type="image", prompt=f"{request.subject_name} abstract aesthetic background",
                                         provider=request.provider, allow_fallback=request.allow_fallback,
                                         project_id=request.project_id, scene_index=request.scene_index, slot=request.slot,
                                         scene_id=_scene_id_of(request))
    try:
        outcome = await ai_media.generate(db, current_user, fallback)
    except GenerationFailed:
        raise HTTPException(status_code=502, detail="The image could not be generated.")
    return _image_result(outcome.asset, current_user, cache_hit=outcome.cache_hit, generated=outcome.generated,
                         fallback=True, **outcome.info())

class AudioRequest(BaseModel):
    text: str
    voice: str = "en-US-GuyNeural"  # Default fallback
    tts_engine: str = "default"

class ImageUploadRequest(BaseModel):
    base64_data: str

class HistoryRequest(BaseModel):
    subject_name: str
    unit_name: str | None = None
    session_number: str | None = None
    session_title: str | None = None
    concept_map: list | None = None
    scenes: list | None = None
    companion_sheet: str | None = None
    source_document: dict | None = None  # Phase 11: {document_id, analysis_id} of the prepared source it was generated from
    cinematic_style: dict | None = None  # Phase 17: the lesson's video style {style, style_version, style_overrides}
    editor: dict | None = None  # Phase 19: the lesson's editor data {version, captions, generated_order}
    studio: dict | None = None  # Phase 20: the Studio's data {version, generated, checkpoints} (studio.clean_studio)

@app.post("/generate-audio")
async def generate_audio(request: AudioRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = await _generate_audio(request, current_user)
    return asset_library.attach(db, result, "audio_url", owner_id=current_user.id, source="narration",
                                details={"voice": request.voice, "engine": request.tts_engine, "text": request.text.strip()[:200]})

async def _generate_audio(request: AudioRequest, current_user: models.User):
    text = request.text.strip()
    voice = request.voice
    tts_engine = request.tts_engine
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    # Cache based on text, voice, and engine hash
    hash_str = f"{text}_{voice}_{tts_engine}"
    text_hash = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()[:16]
    
    # Gemini returns .wav. OpenAI defaults to .mp3, but we will convert it to .wav using ffmpeg to prevent corrupted headers and MP3 padding.
    file_ext = "wav" if (tts_engine.startswith("gemini") or tts_engine.startswith("openai")) else "mp3"
    filename = f"audio_{text_hash}.{file_ext}"
    filepath = os.path.join(STATIC_DIR, filename)

    # Test servers only (AI_FAKE_PROVIDER=1 and FAKE_TTS=1): a speech-like tone instead of a TTS service, so the
    # browser checks never reach the network; its loud and quiet parts give the presenter's mouth real speech timing
    if os.getenv("AI_FAKE_PROVIDER") == "1" and os.getenv("FAKE_TTS") == "1":
        if not os.path.exists(filepath):
            seconds = max(1.0, min(30.0, len(text) / 15))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds:.2f}",
                            "-af", "volume='if(lt(mod(t,0.5),0.3),0.8,0.01)':eval=frame", "-ar", "24000", filepath], check=True, timeout=60)
        return {"status": "success", "audio_url": f"/static/{filename}"}
    
    if os.path.exists(filepath):
        if os.path.getsize(filepath) > 0:
            # Invalidate raw PCM files that lack the RIFF WAV header
            if filepath.endswith(".wav"):
                with open(filepath, "rb") as f:
                    header = f.read(4)
                if header != b'RIFF':
                    print(f"Invalidating raw PCM cache without WAV header: {filepath}")
                    os.remove(filepath)
            
            if os.path.exists(filepath):
                print(f"\n[CACHE HIT] Instantly returning cached audio for: '{text[:30]}...'")
                return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
        else:
            os.remove(filepath)
            
    try:
        if tts_engine.startswith("gemini"):
            # Use the dedicated TTS preview model for audio generation
            actual_model = 'gemini-2.5-flash-preview-tts'
            print(f"\n[GEMINI TTS] Generating voice '{voice}' using model '{actual_model}'...")
            from google import genai
            from google.genai import types
            
            # Key rotation logic
            keys = []
            for i in ["", "_2", "_3", "_4", "_5"]:
                k = os.getenv(f"GEMINI_API_KEY{i}")
                if k: keys.append(k)
                
            if not keys:
                raise Exception("GEMINI_API_KEY is not set in the .env file.")
                
            # The preview TTS model requires explicit instructions to read text
            prompt_text = f"Please read the following text aloud with high energy, a natural conversational tone, and a fluent Indian English accent:\n{text}"
            
            response = None
            last_error = None
            
            for key in keys:
                try:
                    client = genai.Client(api_key=key)
                    response = client.models.generate_content(
                        model=actual_model,
                        contents=prompt_text,
                        config=types.GenerateContentConfig(
                            response_modalities=["AUDIO"],
                            safety_settings=[
                                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                            ],
                            speech_config=types.SpeechConfig(
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=voice
                                    )
                                )
                            )
                        )
                    )
                    break # Success!
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        print(f"\n[WARNING] API Key rate limited (429). Rotating to next key...")
                        continue
                    else:
                        raise e # Other error
            
            if response is None:
                raise last_error
            
            audio_data = None
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                            audio_data = part.inline_data.data
                            break
                if audio_data:
                    break
                    
            if not audio_data:
                raise Exception(f"No audio data returned. Response: {response}")
                
            if tts_engine.startswith("gemini") and not audio_data.startswith(b'RIFF'):
                import wave
                with wave.open(filepath, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2) # 16-bit
                    wav_file.setframerate(24000)
                    wav_file.writeframes(audio_data)
            else:
                with open(filepath, "wb") as f:
                    f.write(audio_data)
                
            return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
            
        elif tts_engine.startswith("openai"):
            print(f"\n[OPENAI TTS] Generating voice '{voice}' using model '{tts_engine}'...")
            import openai
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise Exception("OPENAI_API_KEY is not set in the .env file.")
            
            client = openai.AsyncOpenAI(api_key=api_key)
            
            if "gpt-4o-mini" in tts_engine:
                response = await client.chat.completions.create(
                    model="gpt-4o-audio-preview",
                    modalities=["text", "audio"],
                    audio={"voice": voice, "format": "wav"},
                    messages=[
                        {"role": "user", "content": f"Please read the following text aloud with high energy, a natural conversational tone, and a fluent Indian English accent:\n{text}"}
                    ]
                )
                import base64
                wav_bytes = base64.b64decode(response.choices[0].message.audio.data)
                with open(filepath, "wb") as f:
                    f.write(wav_bytes)
            else:
                actual_model = "tts-1-hd" if "hd" in tts_engine else "tts-1"
                
                response = await client.audio.speech.create(
                    model=actual_model,
                    voice=voice,
                    input=text,
                    response_format="mp3"
                )
                
                temp_mp3 = filepath + ".temp.mp3"
                with open(temp_mp3, "wb") as f:
                    f.write(response.content)
                
                import asyncio
                process = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-y', '-i', temp_mp3, filepath,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await process.communicate()
                
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
            
            return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
            
        elif tts_engine.startswith("elevenlabs"):
            print(f"\n[ELEVENLABS TTS] Generating voice '{voice}'...")
            import httpx
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                raise Exception("ELEVENLABS_API_KEY is not set in the .env file.")
            
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": api_key
            }
            data = {
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data, headers=headers, timeout=60.0)
                if response.status_code != 200:
                    raise Exception(f"ElevenLabs API Error {response.status_code}: {response.text}")
                
                with open(filepath, "wb") as f:
                    f.write(response.content)
                    
            return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
            
        else:
            # Default Edge-TTS fallback
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(filepath)
            return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
    except Exception as e:
        engine_name = "OpenAI" if tts_engine.startswith("openai") else "ElevenLabs" if tts_engine.startswith("elevenlabs") else "Gemini" if tts_engine.startswith("gemini") else "TTS Engine"
        print(f"\n[WARNING] {engine_name} failed with error: {e}")
        print(f"[INFO] Falling back to default Edge-TTS for this sentence...")
        try:
            communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
            await communicate.save(filepath)
            return {"status": "success", "audio_url": f"/static/{filename}?t={int(time.time())}"}
        except Exception as fallback_err:
            raise HTTPException(status_code=500, detail=f"Gemini and fallback TTS both failed: {fallback_err}")

@app.post("/upload-image")
async def upload_image(request: ImageUploadRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = await _upload_image(request, current_user)
    return asset_library.attach(db, result, "url", owner_id=current_user.id, source="upload")

async def _upload_image(request: ImageUploadRequest, current_user: models.User):
    import base64
    import uuid
    
    try:
        b64_string = request.base64_data
        if "," in b64_string:
            b64_string = b64_string.split(",", 1)[1]
            
        # Fix missing padding if any
        b64_string += "=" * ((4 - len(b64_string) % 4) % 4)
        
        image_data = base64.b64decode(b64_string)
        filename = f"img_{uuid.uuid4()}.png"
        filepath = os.path.join(STATIC_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(image_data)
            
        print(f"\n[IMAGE EXTRACTED] Saved DOCX image to {filepath}")
        return {"status": "success", "url": f"/static/{filename}"}
        
    except Exception as e:
        print(f"[ERROR] Image upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

STATIC_VIDEOS_DIR = STATIC_DIR  # alias

@app.post("/upload-media")
async def upload_media(file: UploadFile = File(...), current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        os.makedirs(STATIC_VIDEOS_DIR, exist_ok=True)
        # The client's file name is reduced to a plain, safe name: "../" in it must not leave the folder
        filename = f"{uuid.uuid4().hex[:8]}_{display_name(file.filename, 'upload').replace(' ', '_')}"
        filepath = os.path.join(STATIC_VIDEOS_DIR, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        print(f"\n[MEDIA UPLOADED] Saved user media to {filepath}")
        result = {"status": "success", "url": f"/static/{filename}"}
    except Exception as e:
        print(f"[ERROR] Media upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    return asset_library.attach(db, result, "url", owner_id=current_user.id, source="upload",
                                details={"original_name": display_name(file.filename, 'upload')})

def save_lesson(db, user, fields):
    """A NEW lesson row (a history entry) from the /save-history fields: only the listed keys are kept, each cleaned —
    the prepared source it was written from (Phase 11), its video style (Phase 17), its editor data (Phase 19) and its
    Studio data (Phase 20) only when valid. Used by /save-history and by the Studio's lesson writing (Phase 20), with the
    same behaviour. 422 (HTTPException) for numbers that JSON cannot keep. Returns the Project."""
    payload = {
        "subject_name": fields.get("subject_name"),
        "unit_name": fields.get("unit_name"),
        "session_number": fields.get("session_number"),
        "session_title": fields.get("session_title"),
        "concept_map": fields.get("concept_map"),
        "scenes": fields.get("scenes"),
        "companion_sheet": fields.get("companion_sheet")
    }
    source = fields.get("source_document") or {}
    if all(isinstance(source.get(k), str) and re.fullmatch(r"[0-9a-f]{32}", source[k]) for k in ("document_id", "analysis_id")):
        payload["source_document"] = {"document_id": source["document_id"], "analysis_id": source["analysis_id"]}
    import styles  # Phase 17: the lesson keeps its video style (cleaned: an unknown family or override is never stored)
    style_choice = styles.lesson_choice(fields.get("cinematic_style"))
    if style_choice:
        payload["cinematic_style"] = style_choice
    import editor_api  # Phase 19: the lesson's editor data travels with every version (cleaned)
    editor_data = editor_api.clean_lesson_editor(fields.get("editor"))
    if editor_data:
        payload["editor"] = editor_data
    studio_data = _studio.clean_studio(fields.get("studio"))  # Phase 20: how the Studio made it, and its checkpoints
    if studio_data:
        payload["studio"] = studio_data

    try:
        json.dumps(payload, allow_nan=False)  # NaN / Infinity would make the saved lesson unreadable
    except ValueError:
        raise HTTPException(status_code=422, detail="The lesson contains numbers that cannot be saved.")
    new_project = models.Project(
        user_id=user.id,
        subject_name=fields.get("subject_name"),
        unit_name=fields.get("unit_name"),
        session_number=fields.get("session_number"),
        session_title=fields.get("session_title"),
        json_data=json.dumps(payload)
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    # Note which library assets this lesson uses, so they are never deleted while it exists
    try:
        asset_library.record_project_references(db, new_project, user.id)
    except Exception as e:
        db.rollback()
        print(f"[ASSETS] Could not record the assets of project {new_project.id}: {e}")

    print(f"\n[HISTORY SAVED] Project ID {new_project.id} saved for user {user.username}")
    return new_project

@app.post("/save-history")
def save_history(request: HistoryRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_project = save_lesson(db, current_user, request.model_dump())
    # Return a URL that the frontend can use to load this project via query param
    return {"status": "success", "id": new_project.id, "url": f"/?project_id={new_project.id}"}

# End-to-End Studio (Phase 20): durable lesson writing (a run of kind "lesson_script" on the same runs, leases and recovery),
# saved through save_lesson, and each lesson's workflow state derived from what is saved (studio.py)
lesson_script_service = _studio.LessonScriptService(save_lesson)
ai_recovery.register("lesson_script", lesson_script_service)
_STUDIO_PLANS = {}  # (user, scenes digest, library signature, cache signature, 10-minute bucket) -> plans
_STUDIO_PLANS_MAX = 64


def _studio_planner(db, user, scenes):
    """The Visual Router's plans for a lesson (the library, earlier results, the AI cache; it never generates): the Studio
    counts a visual the router can already show as ready (the page's own plans are not stored with the lesson).
    Phase 21: kept while the lesson's scenes, the visuals the router may choose from (the user's and shared ready assets) and
    the AI cache are unchanged (the Studio asks every few seconds while media is made; planning a 200-scene lesson against a
    large library measured up to ~1 s); refreshed at least every 10 minutes."""
    import hashlib
    from sqlalchemy import func
    from visuals import PlanOptions, plan_lesson
    library = db.query(func.count(models.Asset.id), func.max(models.Asset.updated_at)).filter(
        models.Asset.status == "ready", models.Asset.kind.in_(("image", "video")),
        (models.Asset.owner_id == user.id) | (models.Asset.owner_id.is_(None))).one()
    cached = db.query(func.count(models.AIGeneration.id), func.max(models.AIGeneration.id)).filter(
        models.AIGeneration.scope_key.in_((f"user:{user.id}", "system"))).one()
    digest = hashlib.sha256(json.dumps(scenes, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    key = (user.id, digest, tuple(map(str, library)), tuple(map(str, cached)), int(time.time() // 600))
    plans = _STUDIO_PLANS.get(key)
    if plans is None:
        plans = {(p.scene_index, p.slot): p for p in plan_lesson(db, user, asset_library, scenes, PlanOptions(), _asset_link(user), ai_cache, ai_media)}
        if len(_STUDIO_PLANS) >= _STUDIO_PLANS_MAX:
            _STUDIO_PLANS.pop(next(iter(_STUDIO_PLANS)))
        _STUDIO_PLANS[key] = plans
    return plans

app.include_router(_studio.create_studio_router(get_current_user, document_service, save_lesson, asset_library,
                                                service=lesson_script_service, planner=_studio_planner))

@app.get("/get-history")
def get_history(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(models.Project).filter(models.Project.user_id == current_user.id).order_by(models.Project.updated_at.desc()).all()
    history = []
    for p in projects:
        history.append({
            "id": p.id,
            "url": f"/?project_id={p.id}",
            "name": f"{p.subject_name} - {p.unit_name or ''} {p.session_number or ''}",
            "timestamp": int(p.updated_at.timestamp())
        })
    return {"history": history}

@app.get("/api/projects/{project_id}")
def get_project(project_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    import json
    return json.loads(project.json_data)

class ScriptRequest(BaseModel):
    prompt_text: str
    model_name: str = "gemini-2.5-flash"
    api_provider: str = "gemini"

@app.post("/generate-script")
async def generate_script(request: ScriptRequest, current_user: models.User = Depends(get_current_user)):
    import json
    import re
    from fastapi.responses import StreamingResponse
    import traceback
    
    async def stream_generator():
        try:
            yield "STATUS:STARTED\n"
            raw_text = ""
            total_chars = 0
            chunk_count = 0
            
            if request.api_provider == "openai":
                import openai
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    yield "ERROR:OPENAI_API_KEY is not set in the .env file.\n"
                    return
                client = openai.AsyncOpenAI(api_key=api_key)
                
                # OpenAI handles structured output well via json_object format
                response_stream = await client.chat.completions.create(
                    model=request.model_name,
                    messages=[{"role": "user", "content": request.prompt_text}],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    stream=True
                )
                
                async for chunk in response_stream:
                    if chunk.choices and len(chunk.choices) > 0:
                        content = chunk.choices[0].delta.content
                        if content:
                            raw_text += content
                            total_chars += len(content)
                            chunk_count += 1
                            yield f"PROGRESS:{total_chars}\n"
                            if chunk_count % 5 == 0:
                                await ws_manager.broadcast(f"OPENAI_PROGRESS:{total_chars}")
            else:
                # Gemini logic with Queue
                from google import genai
                from google.genai import types
                import httpx
                import queue
                import threading
                
                api_key = os.getenv("GEMINI_API_KEY")
                if not api_key:
                    yield "ERROR:GEMINI_API_KEY is not set in the .env file.\n"
                    return
                    
                client = genai.Client(api_key=api_key)
                if hasattr(client._api_client, '_async_httpx_client') and client._api_client._async_httpx_client:
                    client._api_client._async_httpx_client.timeout = httpx.Timeout(1200.0)
                if hasattr(client._api_client, '_httpx_client') and client._api_client._httpx_client:
                    client._api_client._httpx_client.timeout = httpx.Timeout(1200.0)
                    
                q = queue.Queue()
                def sync_worker():
                    try:
                        response_stream = client.models.generate_content_stream(
                            model=request.model_name,
                            contents=request.prompt_text,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                max_output_tokens=65536,
                                temperature=0.2
                            )
                        )
                        for chunk in response_stream:
                            if chunk.text: q.put(("chunk", chunk.text))
                        q.put(("done", None))
                    except Exception as e:
                        q.put(("error", e))
                
                thread = threading.Thread(target=sync_worker)
                thread.start()
                
                while True:
                    await asyncio.sleep(0.02)
                    try:
                        msg_type, data = q.get_nowait()
                    except queue.Empty:
                        continue
                        
                    if msg_type == "chunk":
                        raw_text += data
                        total_chars += len(data)
                        chunk_count += 1
                        yield f"PROGRESS:{total_chars}\n"
                        if chunk_count % 5 == 0:
                            await ws_manager.broadcast(f"GEMINI_PROGRESS:{total_chars}")
                    elif msg_type == "done":
                        break
                    elif msg_type == "error":
                        yield f"ERROR:{str(data)}\n"
                        return

            # Both providers hit this common parsing logic
            print(f"\n[{request.api_provider.upper()} RESPONSE] Received {total_chars} chars total")
            raw_text = raw_text.strip()
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
            if match:
                raw_text = match.group(1).strip()
            else:
                raw_text = raw_text.strip()
                
            try:
                parsed, _ = json.JSONDecoder().raw_decode(raw_text.lstrip())
                final_json_string = json.dumps(parsed)
                yield f"FINAL_JSON:{final_json_string}\n"
            except Exception as parse_err:
                print(f"[PARSE ERROR] {parse_err}")
                try:
                    with open("debug_failed_json.txt", "w", encoding="utf-8") as f:
                        f.write(raw_text)
                    print("Dumped failed JSON to debug_failed_json.txt")
                except: pass
                yield f"ERROR:Failed to parse JSON response: {str(parse_err)}\n"
                
        except Exception as e:
            traceback.print_exc()
            yield f"ERROR:{str(e)}\n"

    return StreamingResponse(stream_generator(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    print("Starting Manim Rendering Backend on http://127.0.0.1:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
