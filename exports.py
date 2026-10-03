"""Lesson video exports: job records, resumable upload, persistent storage and download.

The browser (export.js) records the lesson and drives the job:

  POST  /api/exports                  create a job (QUEUED)
  PATCH /api/exports/{id}             browser-side progress: PREPARING, RECORDING, UPLOADING, FAILED, CANCELLED
  GET   /api/exports/{id}/upload      bytes received so far (to resume an upload)
  PUT   /api/exports/{id}/upload      append one chunk of the recording at ?offset=
  POST  /api/exports/{id}/complete    validate and store the file -> PROCESSING -> COMPLETED
  GET   /api/exports[?project_id=]    the user's exports, newest first
  GET   /api/exports/{id}             one export
  POST  /api/exports/{id}/link        short-lived signed URLs for <video> preview and download links
  GET   /api/exports/{id}/download    the file, for its owner only (bearer token or signed link)
  GET   /api/exports/{id}/outputs/{kind}   mp4 | vtt | chapters made from a finished export (Phase 7)
  POST  /api/exports/{id}/outputs/mp4      make (or retry) the MP4 copy

With /complete the browser sends the recording's timeline (scene starts, narration lines as shown).
The server then writes WebVTT subtitles and a chapter list at once and makes an MP4 copy (H.264/AAC,
subtitles and chapters embedded) in the background when its ffmpeg can; the WebM stays the main
file whatever happens to the MP4 (export_outputs.py). Finished videos are also registered in the
asset library (source "export").

Phase 20: a lesson export's timeline may carry {lesson: {project_id, fingerprint, revision}} (the lesson
and the version of it that was recorded). It is kept at the start of the stored timeline file, only for
the export's own lesson; listings return it as `lesson_fingerprint`, and latest_lesson_export() gives the
Studio a lesson's newest export with it.

Files live under EXPORTS_DIR (default ./exports): uploads in progress in tmp/, finished videos in
<user_id>/<export_id>.<ext>. Point EXPORTS_DIR at a persistent volume in production.
"""
import asyncio
import datetime
import json
import os
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import export_outputs
import models
from access import authenticate_media_request, make_link_token
from database import SessionLocal, get_db
from media import audio_peak_db, probe, remux, sniff_container
from storage import LocalStorage, StorageError

# Empty values (e.g. "EXPORTS_DIR=" in .env) fall back to the defaults
EXPORTS_DIR = os.path.abspath(os.getenv("EXPORTS_DIR") or "exports")
STORAGE = LocalStorage(EXPORTS_DIR)
# Long 1080p lessons recorded at 15 Mbit/s run to several GB; this only stops runaway uploads
MAX_EXPORT_BYTES = int(os.getenv("EXPORT_MAX_BYTES") or 8 * 1024 ** 3)
STALE_AFTER = datetime.timedelta(hours=float(os.getenv("EXPORT_STALE_HOURS") or 24))
ORPHAN_AFTER = datetime.timedelta(hours=1)
CLEANUP_EVERY = datetime.timedelta(hours=1)
LINK_TTL = datetime.timedelta(minutes=30)
SILENT_DB = -60.0  # an audio track that never gets louder than this counts as no sound

ACTIVE = {"QUEUED", "PREPARING", "RECORDING", "UPLOADING", "PROCESSING"}
# Transitions the browser may request. PROCESSING and COMPLETED are set only by /complete.
CLIENT_TRANSITIONS = {
    "QUEUED": {"PREPARING", "RECORDING", "FAILED", "CANCELLED"},
    "PREPARING": {"RECORDING", "FAILED", "CANCELLED"},
    "RECORDING": {"UPLOADING", "FAILED", "CANCELLED"},
    "UPLOADING": {"FAILED", "CANCELLED"},
}
CONTAINER_TYPES = {"webm": "video/webm", "mp4": "video/mp4"}
# Files made from a finished export, by kind: (file suffix, media type, download name suffix)
OUTPUT_FILES = {"mp4": (".mp4", "video/mp4", ".mp4"), "vtt": (".vtt", "text/vtt; charset=utf-8", ".vtt"),
                "chapters": (".chapters.txt", "text/plain; charset=utf-8", "-chapters.txt"),
                "timeline": (".timeline.json", "application/json", ".timeline.json")}
DOWNLOADABLE_OUTPUTS = ("mp4", "vtt", "chapters")
LESSON_HEAD_BYTES = 1024  # the lesson link opens a timeline file (export_outputs.clean_timeline); only this much is read
LESSON_OPENING = '{"lesson": '
LESSON_READS = 50  # finished videos per listing whose lesson link is read (newest first)

_last_cleanup = {"at": None}


def _now():
    return datetime.datetime.utcnow()


def _iso(dt):
    return dt.replace(tzinfo=datetime.timezone.utc).isoformat() if dt else None


def _tmp_path(export_id):
    return STORAGE.path(f"tmp/{export_id}.part")


def _stored_path(storage_key):
    try:
        return STORAGE.path(storage_key)
    except StorageError:
        raise HTTPException(status_code=404, detail="Export not found")


def _received(export_id):
    path = _tmp_path(export_id)
    return os.path.getsize(path) if os.path.exists(path) else 0


def _discard_upload(export_id):
    path = _tmp_path(export_id)
    if os.path.exists(path):
        os.remove(path)


def slugify(text, fallback="lesson"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:80].strip("-") or fallback


def download_name(export):
    return f"aadhi-eduengine-{slugify(export.title)}.{export.format or 'webm'}"


def finalise_upload(export_id, user_id, container):
    """Validates the uploaded recording and moves it to permanent storage. Runs in a worker thread."""
    tmp = _tmp_path(export_id)
    info = probe(tmp)
    if info is not None and not info["has_video"]:
        raise ValueError("it has no video track")
    key = f"{user_id}/{export_id}.{container}"
    fixed = f"{tmp}.remux.{container}"
    if remux(tmp, fixed):
        final = STORAGE.put_file(key, fixed, move=True)
        os.remove(tmp)
        info = probe(final) or info
    else:
        if os.path.exists(fixed):
            os.remove(fixed)
        final = STORAGE.put_file(key, tmp, move=True)  # keep the recording exactly as uploaded
    if info and info["has_audio"]:
        peak = audio_peak_db(final)
        if peak is not None and peak <= SILENT_DB:
            info["has_audio"] = False  # an audio track with nothing audible in it
    return key, os.path.getsize(final), info


def _completion_path(export_id):
    return STORAGE.path(f"tmp/{export_id}.complete.json")


def _save_completion(export_id, body, container):
    path = _completion_path(export_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"size": body.size, "mime_type": body.mime_type, "duration_seconds": body.duration_seconds,
                   "timeline": body.timeline, "container": container}, f)


def _load_completion(export_id):
    try:
        with open(_completion_path(export_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _complete_export(db, export, container, key, file_size, info, mime_type, duration_seconds, timeline):
    """The export's final record, its subtitles/chapters and the pending MP4 copy (the finishing step, shared
    by /complete and by recovery after a restart)."""
    claimed = (mime_type or "").split(";")[0].strip().lower()
    export.format = container
    export.mime_type = mime_type[:100] if mime_type and claimed == CONTAINER_TYPES[container] else CONTAINER_TYPES[container]
    export.storage_key = key
    export.file_size = file_size
    export.file_name = download_name(export)
    if info:
        export.duration_seconds = info["duration"] or duration_seconds
        export.width, export.height, export.has_audio = info["width"], info["height"], info["has_audio"]
    else:
        export.duration_seconds = duration_seconds
    export.status = "COMPLETED"
    export.stage = "Video ready" if export.has_audio is not False else "Video ready (no sound)"
    export.progress = 1.0
    export.error_message = None
    export.completed_at = _now()
    db.commit()
    timeline = export_outputs.clean_timeline(timeline)
    lesson = timeline.get("lesson")
    if lesson and (export.source != "lesson" or lesson["project_id"] != export.project_id):
        del timeline["lesson"]  # a video only ever stands for the lesson it was exported from
    # Subtitles and chapters now; the MP4 copy and library entries in the background
    try:
        write_text_outputs(db, export, timeline)
    except OSError as e:
        db.rollback()
        print(f"[EXPORT] Could not write the subtitles/chapters of {export.id}: {e}")
    _output(db, export.id, "mp4").status = "pending"
    db.commit()
    path = _completion_path(export.id)
    if os.path.exists(path):
        os.remove(path)


def recover_exports(register_asset=None):
    """At server start (Phase 9): exports interrupted while being finished are finished from what was kept
    (the uploaded file, or the file already moved into place, and the saved /complete request) — never
    uploaded or recorded again; MP4 copies that were pending or being made are made. Idempotent.
    Returns {"finished": n, "failed": n, "mp4": n}."""
    counts = {"finished": 0, "failed": 0, "mp4": 0}
    db = SessionLocal()
    try:
        for export in db.query(models.VideoExport).filter(models.VideoExport.status == "PROCESSING").all():
            saved = _load_completion(export.id)
            container = (saved or {}).get("container")
            key = f"{export.user_id}/{export.id}.{container}" if container else None
            try:
                if key and STORAGE.exists(key):  # already moved into place before the interruption
                    final = STORAGE.path(key)
                    info = probe(final)
                    file_size = os.path.getsize(final)
                    _discard_upload(export.id)
                elif saved and os.path.exists(_tmp_path(export.id)):  # still where the upload left it
                    key, file_size, info = finalise_upload(export.id, export.user_id, container)
                else:
                    raise ValueError("the uploaded recording is no longer on the server")
            except (ValueError, OSError) as e:
                export.status = "FAILED"
                export.error_message = f"The server stopped while saving this video and it could not be finished ({e}). Please export again."
                export.completed_at = _now()
                db.commit()
                if os.path.exists(_completion_path(export.id)):
                    os.remove(_completion_path(export.id))
                counts["failed"] += 1
                continue
            _complete_export(db, export, container, key, file_size, info, saved.get("mime_type"), saved.get("duration_seconds"),
                             saved.get("timeline"))
            counts["finished"] += 1
            print(f"[EXPORT] Finished export {export.id} interrupted by a restart")
        pending = (db.query(models.ExportOutput).join(models.VideoExport, models.VideoExport.id == models.ExportOutput.export_id)
                   .filter(models.ExportOutput.kind == "mp4", models.ExportOutput.status.in_(("pending", "processing")),
                           models.VideoExport.status == "COMPLETED").all())
        export_ids = [row.export_id for row in pending]
    finally:
        db.close()
    for export_id in export_ids:  # one at a time, like the normal background conversion
        make_mp4(export_id, register_asset)
        counts["mp4"] += 1
    return counts


def cleanup_exports(db, now=None, force=False):
    """Conservative housekeeping, at most once an hour. Completed exports are never touched."""
    now = now or _now()
    if not force and _last_cleanup["at"] and now - _last_cleanup["at"] < CLEANUP_EVERY:
        return
    _last_cleanup["at"] = now

    # Jobs that stopped reporting (tab closed mid-recording or mid-upload)
    stale = db.query(models.VideoExport).filter(
        models.VideoExport.status.in_(ACTIVE), models.VideoExport.updated_at < now - STALE_AFTER).all()
    for export in stale:
        export.status = "FAILED"
        export.error_message = export.error_message or "The export was abandoned before it finished."
        export.completed_at = now
        _discard_upload(export.id)
    db.commit()

    # Partial uploads of failed or cancelled jobs, and upload files with no job at all
    for key in STORAGE.list("tmp"):
        name = key.split("/")[-1]
        if name.endswith(".complete.json"):  # a finishing request kept for recovery, no longer needed
            export = db.get(models.VideoExport, name[:-len(".complete.json")])
            if export is None or export.status in ("COMPLETED", "FAILED", "CANCELLED"):
                STORAGE.delete(key)
            continue
        if not name.endswith(".part"):
            continue
        export = db.get(models.VideoExport, name[:-len(".part")])
        age = now - datetime.datetime.utcfromtimestamp(STORAGE.mtime(key))
        if (export is None and age > ORPHAN_AFTER) or (export is not None and export.status in ("FAILED", "CANCELLED")):
            STORAGE.delete(key)


def outputs_by_export(db, export_ids):
    """{export_id: {kind: ExportOutput}} in one query."""
    found = {}
    if export_ids:
        for row in db.query(models.ExportOutput).filter(models.ExportOutput.export_id.in_(list(export_ids))):
            found.setdefault(row.export_id, {})[row.kind] = row
    return found


def serialize_outputs(outputs):
    return {kind: {"status": row.status, "file_size": row.file_size, "error": row.error_message}
            for kind, row in (outputs or {}).items() if kind in DOWNLOADABLE_OUTPUTS}


def recorded_lesson(storage_key):
    """The lesson link {project_id, fingerprint, revision} at the start of a stored timeline file, or None
    (no link, an older file, a missing or unreadable one). Reads only the first kilobyte."""
    try:
        with open(STORAGE.path(storage_key), "rb") as f:
            head = f.read(LESSON_HEAD_BYTES).decode("utf-8", "ignore")
        if not head.startswith(LESSON_OPENING):
            return None
        value, _ = json.JSONDecoder().raw_decode(head, len(LESSON_OPENING))
    except (OSError, ValueError):  # StorageError is a ValueError too
        return None
    return export_outputs.clean_lesson_link(value)


def recorded_fingerprints(exports_list, outputs, limit=LESSON_READS):
    """{export_id: fingerprint of the lesson it shows} for finished lesson exports whose stored timeline links
    them to their own lesson; at most `limit` files are read, in the order given (newest first in listings)."""
    found, reads = {}, 0
    for export in exports_list:
        timeline = (outputs.get(export.id) or {}).get("timeline")
        if (export.status != "COMPLETED" or export.source != "lesson" or not export.project_id
                or not timeline or timeline.status != "ready" or not timeline.storage_key):
            continue
        if reads >= limit:
            break
        reads += 1
        link = recorded_lesson(timeline.storage_key)
        if link and link["project_id"] == export.project_id:
            found[export.id] = link["fingerprint"]
    return found


def latest_lesson_export(db, user_id, project_id, completed=False):
    """The newest export of one of the user's lessons (source "lesson": manual recordings are not exports of
    the lesson), for the Studio's lesson state (Phase 20): {id, status, completed_at, fingerprint}, or None when
    the lesson has none. completed=True: the newest COMPLETED one. fingerprint is the lesson fingerprint the
    video was recorded from (kept in its timeline) for a COMPLETED export that has it, else None (older videos,
    missing or unreadable files). An active export abandoned for longer than STALE_AFTER counts as FAILED, as
    the next history listing will record it. Read only: never writes or commits."""
    query = db.query(models.VideoExport).filter(models.VideoExport.user_id == user_id,
                                                models.VideoExport.project_id == project_id,
                                                models.VideoExport.source == "lesson")
    if completed:
        query = query.filter(models.VideoExport.status == "COMPLETED")
    export = query.order_by(models.VideoExport.created_at.desc()).first()
    if export is None:
        return None
    status = export.status
    if status in ACTIVE and export.updated_at and export.updated_at < _now() - STALE_AFTER:
        status = "FAILED"
    fingerprint = recorded_fingerprints([export], outputs_by_export(db, [export.id]), limit=1).get(export.id)
    return {"id": export.id, "status": status, "completed_at": _iso(export.completed_at), "fingerprint": fingerprint}


def serialize(export, outputs=None, lesson_fingerprint=None):
    return {
        "id": export.id,
        "project_id": export.project_id,
        "title": export.title,
        "source": export.source,
        "status": export.status,
        "stage": export.stage,
        "progress": export.progress,
        "format": export.format,
        "mime_type": export.mime_type,
        "file_name": export.file_name,
        "file_size": export.file_size,
        "duration_seconds": export.duration_seconds,
        "width": export.width,
        "height": export.height,
        "has_audio": export.has_audio,
        "error_message": export.error_message,
        "retry_of_id": export.retry_of_id,
        "created_at": _iso(export.created_at),
        "updated_at": _iso(export.updated_at),
        "completed_at": _iso(export.completed_at),
        "outputs": serialize_outputs(outputs),
        "lesson_fingerprint": lesson_fingerprint,  # Phase 20: the lesson version the video shows, when known
    }


def serialize_finished(db, exports_list):
    """serialize() of each export with its outputs (one query) and the lesson fingerprint it was recorded from."""
    outputs = outputs_by_export(db, [e.id for e in exports_list])
    fingerprints = recorded_fingerprints(exports_list, outputs)
    return [serialize(e, outputs.get(e.id), fingerprints.get(e.id)) for e in exports_list]


def _output(db, export_id, kind):
    row = db.query(models.ExportOutput).filter(models.ExportOutput.export_id == export_id, models.ExportOutput.kind == kind).first()
    if not row:
        row = models.ExportOutput(export_id=export_id, kind=kind)
        db.add(row)
    return row


def write_text_outputs(db, export, timeline):
    """Subtitles, chapters and the timeline of a finished export, from the timeline the browser sent."""
    base = export.storage_key.rsplit(".", 1)[0]
    duration = export.duration_seconds
    files = {"timeline": json.dumps(timeline), "vtt": export_outputs.build_vtt(timeline["cues"], duration),
             "chapters": export_outputs.chapters_text(export_outputs.build_chapters(timeline["scenes"], duration))}
    for kind, text in files.items():
        key = base + OUTPUT_FILES[kind][0]
        path = STORAGE.path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        row = _output(db, export.id, kind)
        row.status, row.storage_key, row.file_size, row.mime_type, row.error_message = \
            "ready", key, os.path.getsize(path), OUTPUT_FILES[kind][1], None
    db.commit()


def make_mp4(export_id, register_asset=None):
    """Background job: the MP4 copy of a finished export (and the library assets of its videos).
    The WebM is never touched; a failure is recorded on the mp4 output only."""
    db = SessionLocal()
    try:
        export = db.get(models.VideoExport, export_id)
        if not export or export.status != "COMPLETED" or not export.storage_key:
            return
        if register_asset:
            _register(db, export, "webm", export.storage_key, register_asset)
        row = _output(db, export.id, "mp4")
        reason = export_outputs.mp4_unavailable_reason()
        if export.format == "mp4":
            reason = "The recording is already an MP4 file."
        if reason:
            row.status, row.error_message = "unavailable", reason
            db.commit()
            return
        row.status, row.error_message = "processing", None
        db.commit()
        base = export.storage_key.rsplit(".", 1)[0]
        outputs = outputs_by_export(db, [export.id]).get(export.id, {})
        src = STORAGE.path(export.storage_key)
        key = base + OUTPUT_FILES["mp4"][0]
        vtt = outputs.get("vtt")
        timeline = outputs.get("timeline")
        meta_path = None
        try:
            if timeline and timeline.status == "ready":
                with open(STORAGE.path(timeline.storage_key), encoding="utf-8") as f:
                    scenes = json.load(f).get("scenes", [])
                meta_path = STORAGE.path(base + ".ffmetadata.txt")
                with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(export_outputs.chapters_ffmetadata(export_outputs.build_chapters(scenes, export.duration_seconds),
                                                               export.duration_seconds or 0))
            vtt_path = STORAGE.path(vtt.storage_key) if vtt and vtt.status == "ready" and vtt.file_size else None
            export_outputs.convert_to_mp4(src, STORAGE.path(key), duration=export.duration_seconds,
                                          vtt_path=vtt_path if vtt_path and _has_cues(vtt_path) else None, ffmeta_path=meta_path)
        except (export_outputs.ConversionError, OSError, ValueError) as e:
            row.status, row.error_message = "failed", f"The MP4 copy could not be made: {e}. The WebM video is complete."
            db.commit()
            return
        finally:
            if meta_path and os.path.exists(meta_path):
                os.remove(meta_path)
        row.status, row.storage_key, row.file_size, row.mime_type, row.error_message = \
            "ready", key, os.path.getsize(STORAGE.path(key)), "video/mp4", None
        db.commit()
        if register_asset:
            _register(db, export, "mp4", key, register_asset)
    finally:
        db.close()


def _has_cues(vtt_path):
    with open(vtt_path, encoding="utf-8") as f:
        return "-->" in f.read()


def _register(db, export, kind, key, register_asset):
    """The export's video file as a library asset (never breaks the export)."""
    try:
        asset = register_asset(db, key, export)
        if asset:
            row = _output(db, export.id, kind)
            row.asset_id = asset.id
            if kind == "webm":
                row.status, row.storage_key, row.file_size, row.mime_type = "ready", key, export.file_size, export.mime_type
            db.commit()
    except Exception as e:  # noqa: BLE001 - the video is saved; the library entry is an extra
        db.rollback()
        print(f"[EXPORT] Could not register export {export.id} in the asset library: {e}")


class ExportCreate(BaseModel):
    project_id: int | None = None
    title: str | None = None
    source: str = "lesson"
    retry_of_id: str | None = None


class ExportUpdate(BaseModel):
    status: str | None = None
    stage: str | None = None
    progress: float | None = None
    error_message: str | None = None


class ExportComplete(BaseModel):
    size: int
    mime_type: str | None = None
    duration_seconds: float | None = None  # measured by the browser; used when ffprobe is unavailable
    timeline: dict | None = None  # {scenes: [{t, title}], cues: [{start, end, text}]}, seconds from the recording's start


def create_exports_router(get_current_user, secret_key, algorithm="HS256", register_asset=None):
    """register_asset(db, storage_key, export) -> asset: adds a finished video to the asset library."""
    router = APIRouter(prefix="/api/exports", tags=["exports"])

    def owned_export(export_id, user, db):
        export = db.query(models.VideoExport).filter(
            models.VideoExport.id == export_id, models.VideoExport.user_id == user.id).first()
        if not export:
            # Same answer whether it does not exist or belongs to someone else
            raise HTTPException(status_code=404, detail="Export not found")
        return export

    def fail(export, message, db):
        export.status = "FAILED"
        export.error_message = message
        export.stage = None
        export.progress = None
        export.completed_at = _now()
        db.commit()
        _discard_upload(export.id)

    def authenticate(request, token, export_id, db):
        """A normal login token in the Authorization header, or a signed link (?token=) for this export."""
        return authenticate_media_request(request, token, scope="export", resource_id=export_id, db=db,
                                          secret=secret_key, algorithm=algorithm, noun="video")

    @router.post("")
    def create_export(body: ExportCreate, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        cleanup_exports(db)
        project = None
        if body.project_id is not None:
            project = db.query(models.Project).filter(
                models.Project.id == body.project_id, models.Project.user_id == user.id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
        if body.retry_of_id:
            owned_export(body.retry_of_id, user, db)
        title = (body.title or "").strip()[:200]
        if not title and project:
            title = " ".join(filter(None, [project.subject_name, project.session_number, project.session_title]))
        export = models.VideoExport(
            id=uuid.uuid4().hex,
            project_id=project.id if project else None,
            user_id=user.id,
            title=title or "Lesson",
            source=body.source if body.source in ("lesson", "manual") else "lesson",
            status="QUEUED",
            stage="Waiting to start",
            retry_of_id=body.retry_of_id,
        )
        db.add(export)
        db.commit()
        db.refresh(export)
        return serialize(export)

    @router.get("")
    def list_exports(project_id: int | None = None, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        cleanup_exports(db)
        query = db.query(models.VideoExport).filter(models.VideoExport.user_id == user.id)
        if project_id is not None:
            query = query.filter(models.VideoExport.project_id == project_id)
        exports = query.order_by(models.VideoExport.created_at.desc()).limit(200).all()
        return {"exports": serialize_finished(db, exports)}

    @router.get("/{export_id}")
    def get_export(export_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        return serialize_finished(db, [export])[0]

    @router.patch("/{export_id}")
    def update_export(export_id: str, body: ExportUpdate, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        if export.status not in ACTIVE:
            raise HTTPException(status_code=409, detail=f"This export has already finished ({export.status}).")
        if body.status and body.status != export.status:
            if body.status not in CLIENT_TRANSITIONS.get(export.status, set()):
                raise HTTPException(status_code=409, detail=f"An export cannot go from {export.status} to {body.status}.")
            export.status = body.status
            export.progress = None
            if body.status in ("FAILED", "CANCELLED"):
                export.completed_at = _now()
                _discard_upload(export.id)
        if body.stage is not None:
            export.stage = body.stage[:255]
        if body.progress is not None:
            export.progress = min(max(body.progress, 0.0), 1.0)
        if body.error_message is not None:
            export.error_message = body.error_message[:2000]
        db.commit()
        return serialize(export)

    @router.get("/{export_id}/upload")
    def upload_status(export_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        return {"received": _received(export.id), "status": export.status}

    @router.put("/{export_id}/upload")
    async def upload_chunk(export_id: str, offset: int, request: Request, total: int | None = None,
                           user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        if export.status != "UPLOADING":
            raise HTTPException(status_code=409, detail="This export is not accepting uploads.")
        received = _received(export.id)
        if offset != received:
            # Resume point for the client: it resends from `received`
            raise HTTPException(status_code=409, detail={"message": "Upload offset mismatch", "received": received})
        path = _tmp_path(export.id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        size = received
        # Streamed straight to disk: the chunk is never held in memory as a whole
        with open(path, "ab") as f:
            async for piece in request.stream():
                if size + len(piece) > MAX_EXPORT_BYTES:
                    f.truncate(received)
                    raise HTTPException(status_code=413, detail="The video is larger than this server accepts.")
                f.write(piece)
                size += len(piece)
        if total:
            export.progress = min(size / total, 1.0)
            export.stage = "Uploading video"
            db.commit()
        return {"received": size}

    @router.post("/{export_id}/complete")
    async def complete_upload(export_id: str, body: ExportComplete, background: BackgroundTasks,
                              user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        if export.status != "UPLOADING":
            raise HTTPException(status_code=409, detail="This export is not waiting for an upload.")
        received = _received(export.id)
        if received == 0 or received != body.size:
            # Stays UPLOADING so the browser can resume the missing bytes
            raise HTTPException(status_code=400, detail=f"The upload is incomplete ({received} of {body.size} bytes received).")
        container = sniff_container(_tmp_path(export.id))
        if not container:
            fail(export, "The uploaded file is not a WebM or MP4 video.", db)
            raise HTTPException(status_code=400, detail=export.error_message)

        # What finishing needs, kept beside the upload: a restart during finishing can complete it (Phase 9)
        _save_completion(export.id, body, container)
        export.status = "PROCESSING"
        export.stage = "Saving video"
        export.progress = None
        db.commit()
        try:
            key, file_size, info = await asyncio.to_thread(finalise_upload, export.id, user.id, container)
        except ValueError as e:
            fail(export, f"The recording could not be read as a video: {e}.", db)
            raise HTTPException(status_code=400, detail=export.error_message)
        except OSError as e:
            print(f"[EXPORT] Storage error for {export.id}: {e}")
            fail(export, "The video could not be saved on the server (storage error). Please retry the export.", db)
            raise HTTPException(status_code=500, detail=export.error_message)

        _complete_export(db, export, container, key, file_size, info, body.mime_type, body.duration_seconds, body.timeline)
        background.add_task(make_mp4, export.id, register_asset)
        return serialize_finished(db, [export])[0]

    @router.post("/{export_id}/link")
    def export_link(export_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        export = owned_export(export_id, user, db)
        if export.status != "COMPLETED":
            raise HTTPException(status_code=409, detail="This video is not ready yet.")
        token = make_link_token(secret_key, algorithm, user.username, "export", export.id, LINK_TTL)
        url = f"/api/exports/{export.id}/download?token={token}"
        outputs = outputs_by_export(db, [export.id]).get(export.id, {})
        output_urls = {kind: f"/api/exports/{export.id}/outputs/{kind}?token={token}"
                       for kind, row in outputs.items() if kind in DOWNLOADABLE_OUTPUTS and row.status == "ready"}
        return {"download_url": url, "preview_url": url + "&inline=1", "output_urls": output_urls,
                "expires_in": int(LINK_TTL.total_seconds())}

    @router.get("/{export_id}/outputs/{kind}")
    def download_output(export_id: str, kind: str, request: Request, token: str | None = None, db: Session = Depends(get_db)):
        user = authenticate(request, token, export_id, db)
        export = owned_export(export_id, user, db)
        if kind not in DOWNLOADABLE_OUTPUTS:
            raise HTTPException(status_code=404, detail="No such file for this export.")
        row = outputs_by_export(db, [export.id]).get(export.id, {}).get(kind)
        if not row or row.status != "ready" or not row.storage_key:
            raise HTTPException(status_code=409 if row else 404, detail=(row and row.error_message) or "This file is not ready.")
        path = _stored_path(row.storage_key)
        if not os.path.isfile(path):
            raise HTTPException(status_code=410, detail="The file is missing on the server.")
        name = download_name(export).rsplit(".", 1)[0] + OUTPUT_FILES[kind][2]
        return FileResponse(path, media_type=OUTPUT_FILES[kind][1], filename=name,
                            content_disposition_type="attachment")

    @router.post("/{export_id}/outputs/mp4")
    def retry_mp4(export_id: str, background: BackgroundTasks, user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
        """Makes the MP4 copy again (after a failure, or once the server can)."""
        export = owned_export(export_id, user, db)
        if export.status != "COMPLETED":
            raise HTTPException(status_code=409, detail="This video is not ready yet.")
        row = _output(db, export.id, "mp4")
        if row.status in ("pending", "processing"):
            raise HTTPException(status_code=409, detail="The MP4 copy is already being made.")
        if row.status == "ready":
            raise HTTPException(status_code=409, detail="The MP4 copy is ready.")
        export_outputs._encoder["checked"] = False  # the server may have gained an encoder since
        row.status, row.error_message = "pending", None
        db.commit()
        background.add_task(make_mp4, export.id, register_asset)
        return serialize_finished(db, [export])[0]

    @router.get("/{export_id}/download")
    def download_export(export_id: str, request: Request, token: str | None = None, inline: bool = False,
                        db: Session = Depends(get_db)):
        user = authenticate(request, token, export_id, db)
        export = owned_export(export_id, user, db)
        if export.status != "COMPLETED":
            raise HTTPException(status_code=409, detail="This video is not ready yet.")
        path = _stored_path(export.storage_key) if export.storage_key else None
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=410, detail="The video file is missing on the server. Please export the lesson again.")
        return FileResponse(path, media_type=CONTAINER_TYPES.get(export.format, "application/octet-stream"),
                            filename=export.file_name or download_name(export),
                            content_disposition_type="inline" if inline else "attachment")

    return router
