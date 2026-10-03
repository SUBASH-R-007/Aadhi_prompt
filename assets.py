"""Asset library: reusable lesson media with stable IDs.

An image, sound or video is registered once and then referred to by its asset ID from any
lesson (e.g. scene.video_asset_id, or <img src="asset:ID"> in a scene's HTML). The page resolves
IDs to short-lived URLs through this API, so lessons and the video exporter never depend on where
a file is stored.

Storage volumes (all local disk today; see storage.py):
  assets  ASSETS_DIR (default ./assets): files uploaded to the library, stored once per content
          as blobs/<sha256[:2]>/<sha256>.<ext>
  static  static_videos/: files the generators already write (narration, Manim renders, AI
          videos, uploads), registered where they are
  system  video_template/: Aadhi's clips and the intro media shipped with the app
  exports EXPORTS_DIR: finished lesson videos (Phase 7), registered where the export stored them

Files in 'static' and 'system' are registered in place and never deleted by the library; a file
in 'assets' is removed only when no remaining asset uses it.

API (all require login; content also accepts a signed link):
  POST   /api/assets               upload a file (validated, deduplicated by content)
  GET    /api/assets               list the user's assets and the shared system assets
  GET    /api/assets/{id}          one asset, with the saved lessons that use it
  PATCH  /api/assets/{id}          description and keywords of your own asset (for visual matching)
  DELETE /api/assets/{id}          delete an unused asset of your own
  POST   /api/assets/resolve       asset IDs -> short-lived content URLs (used by lessons/exports)
  GET    /api/assets/{id}/content  the file itself
"""
import datetime
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from access import authenticate_media_request, make_link_token
from database import get_db
from media import MediaError, inspect_media
from storage import LocalStorage, StorageError, sha256_file

ASSETS_DIR = os.path.abspath(os.getenv("ASSETS_DIR") or "assets")
MAX_ASSET_BYTES = int(os.getenv("ASSET_MAX_BYTES") or 2 * 1024 ** 3)
LINK_TTL = datetime.timedelta(hours=12)  # long enough for a lesson or an export to play through

KINDS = ("video", "audio", "image")
SOURCES = ("mascot", "narration", "manim", "ai-video", "ai-image", "ai-presenter", "upload", "system", "export")
# pending: registered, file not ready yet (for generators that finish later)
# ready: usable; failed: the file could not be validated or has gone missing; deleted: removed by its owner
STATUSES = ("pending", "ready", "failed", "deleted")

# Where lessons refer to media, besides <img src="asset:ID"> inside a scene's HTML
ASSET_ID_FIELDS = ("video_asset_id", "manim_asset_id")
SIDE_PANEL_ASSET_FIELDS = ("video_asset_id",)
LEGACY_URL_FIELDS = ("video_url", "manim_video_url")
# Public URL prefixes of the volumes that hold registered files
URL_VOLUMES = {"/static/": "static", "/video_template/": "system"}
ASSET_MARKER = re.compile(r"asset:([0-9a-f]{32})")
IMG_SRC = re.compile(r"<img[^>]*?\ssrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _now():
    return datetime.datetime.utcnow()


def _iso(dt):
    return dt.replace(tzinfo=datetime.timezone.utc).isoformat() if dt else None


def scope_key(owner_id):
    return "system" if owner_id is None else f"user:{owner_id}"


def display_name(name, fallback="file"):
    """A file name that is safe to show and to put in a Content-Disposition header."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .")[:120]
    return base or fallback


def serialize(asset, user_id=None, references=None):
    data = {
        "id": asset.id,
        "kind": asset.kind,
        "source": asset.source,
        "scope": "system" if asset.owner_id is None else "private",
        "owned": asset.owner_id is not None and asset.owner_id == user_id,
        "status": asset.status,
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "sha256": asset.sha256,
        "duration_seconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
        "has_audio": asset.has_audio,
        "details": json.loads(asset.details) if asset.details else {},
        "error_message": asset.error_message,
        "created_at": _iso(asset.created_at),
        "updated_at": _iso(asset.updated_at),
    }
    if references is not None:
        data["references"] = references
        data["deletable"] = data["owned"] and references == 0 and asset.status != "deleted"
    return data


class AssetLibrary:
    def __init__(self, volumes, managed="assets"):
        self.volumes = volumes  # name -> LocalStorage
        self.managed = managed

    # ---- registration -------------------------------------------------------------

    def register_file(self, db, path, *, owner_id, source, file_name, details=None, move=False):
        """Validates a local file and adds it to the library, stored once per content.
        Returns (asset, created); created is False when this owner already had the same content."""
        size = os.path.getsize(path)
        if size == 0:
            raise MediaError("the file is empty")
        if size > MAX_ASSET_BYTES:
            raise MediaError("the file is larger than the library accepts")
        info = inspect_media(path)
        digest = sha256_file(path)
        scope = scope_key(owner_id)

        # Same content already in this owner's library: that is the asset (filename is not identity)
        existing = db.query(models.Asset).filter(
            models.Asset.scope_key == scope, models.Asset.sha256 == digest,
            models.Asset.status.in_(("ready", "pending"))).first()
        if existing and self.file_exists(existing):
            return existing, False

        # Each content is stored once: reuse any copy already on disk (another owner's upload, a
        # generator's output, a shipped system file); otherwise keep it as a content-addressed blob
        volume, key = self.managed, f"blobs/{digest[:2]}/{digest}.{info['ext']}"
        for twin in db.query(models.Asset).filter(models.Asset.sha256 == digest, models.Asset.status == "ready"):
            if self.file_exists(twin):
                volume, key = twin.storage_volume, twin.storage_key
                break
        else:
            storage = self.volumes[self.managed]
            if not storage.exists(key):
                storage.put_file(key, path, move=move)
        asset = self._save(db, owner_id=owner_id, volume=volume, key=key, info=info, digest=digest, size=size,
                           source=source, file_name=display_name(file_name), details=details)
        return asset, True

    def adopt(self, db, volume, key, *, owner_id, source, details=None, file_name=None):
        """Registers a file that already exists in `volume` (e.g. a Manim render) without copying it.
        Idempotent: the same unchanged file for the same owner is always the same asset."""
        storage = self.volumes[volume]
        path = storage.path(key)
        if not os.path.isfile(path):
            raise MediaError("the file does not exist")
        size = os.path.getsize(path)
        scope = scope_key(owner_id)
        location = db.query(models.Asset).filter(models.Asset.scope_key == scope, models.Asset.storage_volume == volume,
                                                 models.Asset.storage_key == key).first()
        if location and location.status == "ready" and location.file_size == size:
            return location
        # Another owner registered this exact file: reuse its checked metadata instead of re-reading it
        twin = db.query(models.Asset).filter(models.Asset.storage_volume == volume, models.Asset.storage_key == key,
                                             models.Asset.status == "ready", models.Asset.file_size == size).first()
        if twin:
            info = {"kind": twin.kind, "mime_type": twin.mime_type, "width": twin.width, "height": twin.height,
                    "duration": twin.duration_seconds, "has_audio": twin.has_audio}
            digest = twin.sha256
        else:
            info = inspect_media(path)
            digest = sha256_file(path)
        return self._save(db, owner_id=owner_id, volume=volume, key=key, info=info, digest=digest, size=size,
                          source=source, file_name=display_name(file_name or key.split("/")[-1]), details=details)

    def _save(self, db, *, owner_id, volume, key, info, digest, size, source, file_name, details):
        scope = scope_key(owner_id)
        fields = dict(kind=info["kind"], mime_type=info["mime_type"], file_size=size, sha256=digest,
                      duration_seconds=info.get("duration"), width=info.get("width"), height=info.get("height"),
                      has_audio=info.get("has_audio"), source=source if source in SOURCES else "upload",
                      status="ready", error_message=None, deleted_at=None,
                      details=json.dumps(details) if details else None)
        asset = db.query(models.Asset).filter(models.Asset.scope_key == scope, models.Asset.storage_volume == volume,
                                              models.Asset.storage_key == key).first()
        if asset:
            for name, value in fields.items():  # the file changed, or a deleted asset is registered again
                setattr(asset, name, value)
        else:
            asset = models.Asset(id=uuid.uuid4().hex, scope_key=scope, owner_id=owner_id, storage_volume=volume,
                                 storage_key=key, file_name=file_name, **fields)
            db.add(asset)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request registered the same file first
            db.rollback()
            asset = db.query(models.Asset).filter(models.Asset.scope_key == scope, models.Asset.storage_volume == volume,
                                                  models.Asset.storage_key == key).first()
        db.refresh(asset)
        return asset

    def adopt_url(self, db, url, *, owner_id, source, details=None):
        """The asset for a public media URL such as "/static/abc.mp4" (registering it for `owner_id`
        if needed), or None if the URL is not a file this library can serve."""
        if not isinstance(url, str):
            return None
        path = url.split("?", 1)[0].split("#", 1)[0]
        for prefix, volume in URL_VOLUMES.items():
            if path.startswith(prefix):
                name = path[len(prefix):]
                if not name or "/" in name or "\\" in name or name.startswith("."):
                    return None
                try:
                    if volume == "system":  # shared files are registered by the server, never per user
                        return db.query(models.Asset).filter(models.Asset.scope_key == "system", models.Asset.storage_volume == volume,
                                                             models.Asset.storage_key == name, models.Asset.status == "ready").first()
                    return self.adopt(db, volume, name, owner_id=owner_id, source=source, details=details)
                except (MediaError, StorageError):
                    return None
        return None

    def attach(self, db, result, url_field, *, owner_id, source, details=None):
        """Adds `asset_id` to a generator's JSON response when the file it produced can be registered.
        Registration never breaks the generator itself."""
        try:
            asset = self.adopt_url(db, result.get(url_field), owner_id=owner_id, source=source, details=details)
            if asset:
                result["asset_id"] = asset.id
        except Exception as e:  # noqa: BLE001 - the media was produced; the library is an extra
            db.rollback()
            print(f"[ASSETS] Could not register {result.get(url_field)}: {e}")
        return result

    # ---- access -------------------------------------------------------------------

    def file_path(self, asset):
        return self.volumes[asset.storage_volume].path(asset.storage_key)

    def file_exists(self, asset):
        try:
            return os.path.isfile(self.file_path(asset))
        except (KeyError, StorageError):
            return False

    @staticmethod
    def usable_by(asset, user_id):
        """Whether this user may use the asset: their own, or a shared system asset."""
        return bool(asset) and asset.status != "deleted" and (asset.owner_id is None or asset.owner_id == user_id)

    def accessible(self, db, asset_id, user_id):
        """The asset if this user may use it, else None."""
        if not isinstance(asset_id, str) or not re.fullmatch(r"[0-9a-f]{32}", asset_id):
            return None
        asset = db.get(models.Asset, asset_id)
        return asset if self.usable_by(asset, user_id) else None

    def mark_missing(self, db, asset):
        asset.status = "failed"
        asset.error_message = "The file is missing from storage."
        db.commit()

    def reference_counts(self, db, asset_ids):
        if not asset_ids:
            return {}
        rows = db.query(models.AssetReference.asset_id, func.count(models.AssetReference.id)).filter(
            models.AssetReference.asset_id.in_(asset_ids)).group_by(models.AssetReference.asset_id).all()
        return {asset_id: count for asset_id, count in rows}

    # ---- lessons ------------------------------------------------------------------

    def lesson_refs(self, scenes):
        """(field, asset ID or legacy URL) pairs for the media a lesson's scenes refer to."""
        found = []
        for i, scene in enumerate(scenes if isinstance(scenes, list) else []):
            if not isinstance(scene, dict):
                continue
            where = f"scenes[{i}]"
            for name in ASSET_ID_FIELDS:
                if scene.get(name):
                    found.append((f"{where}.{name}", scene[name]))
            for name in LEGACY_URL_FIELDS:
                if scene.get(name):
                    found.append((f"{where}.{name}", scene[name]))
            panel = scene.get("side_panel") if isinstance(scene.get("side_panel"), dict) else {}
            for name in SIDE_PANEL_ASSET_FIELDS:
                if panel.get(name):
                    found.append((f"{where}.side_panel.{name}", panel[name]))
            if panel.get("video_url"):
                found.append((f"{where}.side_panel.video_url", panel["video_url"]))
            for img_id, value in (scene.get("uploaded_image_assets") or {}).items() if isinstance(scene.get("uploaded_image_assets"), dict) else []:
                found.append((f"{where}.uploaded_image_assets.{img_id}", value))
            for img_id, value in (scene.get("uploaded_images") or {}).items() if isinstance(scene.get("uploaded_images"), dict) else []:
                found.append((f"{where}.uploaded_images.{img_id}", value))
            # Assets the visual router chose for this scene (visuals.py)
            plans = scene.get("visual_plan") if isinstance(scene.get("visual_plan"), dict) else {}
            for slot, plan in plans.items():
                if isinstance(plan, dict) and isinstance(plan.get("asset_id"), str):
                    found.append((f"{where}.visual_plan.{slot}", plan["asset_id"]))
            # The presenter clip the scene shows (Phase 12)
            presenter = scene.get("presenter_plan") if isinstance(scene.get("presenter_plan"), dict) else {}
            media = presenter.get("media") if isinstance(presenter.get("media"), dict) else {}
            if isinstance(media.get("asset_id"), str):
                found.append((f"{where}.presenter_plan.media", media["asset_id"]))
            # The background picture or clip of the scene's composition (Phase 13)
            cinematic = scene.get("cinematic_plan") if isinstance(scene.get("cinematic_plan"), dict) else {}
            background = cinematic.get("background") if isinstance(cinematic.get("background"), dict) else {}
            if isinstance(background.get("asset_id"), str):
                found.append((f"{where}.cinematic_plan.background", background["asset_id"]))
            # Visuals the user chose or approved in Visual Review (Phase 6)
            reviews = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
            for slot, review in reviews.items():
                if isinstance(review, dict) and isinstance(review.get("asset_id"), str):
                    found.append((f"{where}.visual_review.{slot}", review["asset_id"]))
            html = scene.get("html") if isinstance(scene.get("html"), str) else ""
            for asset_id in ASSET_MARKER.findall(html):
                found.append((f"{where}.html", asset_id))
            for src in IMG_SRC.findall(html):
                if not src.startswith("asset:"):
                    found.append((f"{where}.html", src))
        return found

    def record_project_references(self, db, project, user_id):
        """Records which library assets a saved lesson uses (its own and shared ones only), so an
        asset in use is never deleted. Files linked by plain URL are registered for the owner."""
        db.query(models.AssetReference).filter(models.AssetReference.project_id == project.id).delete()
        payload = json.loads(project.json_data or "{}")
        seen = set()
        for field, value in self.lesson_refs(payload.get("scenes")):
            if not isinstance(value, str):
                continue
            if re.fullmatch(r"[0-9a-f]{32}", value):
                asset = self.accessible(db, value, user_id)
            elif "/api/assets/" in value:
                match = re.search(r"/api/assets/([0-9a-f]{32})/content", value)
                asset = self.accessible(db, match.group(1), user_id) if match else None
            else:
                asset = self.adopt_url(db, value, owner_id=user_id, source=self._source_for(field, value))
            if asset and (asset.id, field) not in seen:
                seen.add((asset.id, field))
                db.add(models.AssetReference(asset_id=asset.id, project_id=project.id, field=field[:160]))
        db.commit()
        return len(seen)

    @staticmethod
    def _source_for(field, url):
        name = url.split("?", 1)[0].rsplit("/", 1)[-1]
        if name.startswith("ai_video_"):
            return "ai-video"
        if name.startswith("audio_"):
            return "narration"
        if "manim" in field:
            return "manim"
        return "upload"

    # ---- deletion -----------------------------------------------------------------

    def delete(self, db, asset):
        """Soft-deletes an unused asset; its file goes only if it is in the managed volume and no
        other asset still uses it. Callers check ownership and references first."""
        asset.status = "deleted"
        asset.deleted_at = _now()
        db.commit()
        if asset.storage_volume != self.managed:
            return False
        still_used = db.query(models.Asset).filter(models.Asset.storage_volume == asset.storage_volume,
                                                   models.Asset.storage_key == asset.storage_key,
                                                   models.Asset.status != "deleted").count()
        if still_used:
            return False
        self.volumes[self.managed].delete(asset.storage_key)
        return True


class ResolveRequest(BaseModel):
    ids: list[str]


class AssetUpdate(BaseModel):
    # What the asset shows, in words: the visual router matches lesson visuals against these
    description: str | None = None
    keywords: list[str] | None = None


# Limits for an asset's description and keywords (assets.js checks the same before saving)
DESCRIPTION_MAX = 1000
KEYWORDS_MAX = 30
KEYWORD_MAX = 60


def clean_text(value):
    """Trimmed, with every run of whitespace (newlines, tabs, double spaces) turned into one space."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def clean_keywords(values):
    """Tidied keywords without empty entries or repeats; a repeat differing only in case is dropped
    (the first spelling is kept). Raises ValueError with a readable reason."""
    if len(values) > KEYWORDS_MAX:
        raise ValueError(f"Use at most {KEYWORDS_MAX} keywords.")
    keywords, seen = [], set()
    for value in values:
        keyword = clean_text(value)
        if not keyword:
            continue
        if len(keyword) > KEYWORD_MAX:
            raise ValueError(f"Keep each keyword to {KEYWORD_MAX} characters or fewer.")
        if keyword.lower() not in seen:
            seen.add(keyword.lower())
            keywords.append(keyword)
    return keywords


def create_assets_router(library, get_current_user, secret_key, algorithm="HS256"):
    router = APIRouter(prefix="/api/assets", tags=["assets"])

    def content_url(asset, user):
        token = make_link_token(secret_key, algorithm, user.username, "asset", asset.id, LINK_TTL)
        return f"/api/assets/{asset.id}/content?token={token}"

    def get_or_404(db, asset_id, user):
        asset = library.accessible(db, asset_id, user.id)
        if not asset:
            # Same answer for unknown IDs and other users' assets
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    @router.post("")
    async def upload_asset(file: UploadFile = File(...), user: models.User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
        storage = library.volumes[library.managed]
        tmp = storage.path(f"tmp/{uuid.uuid4().hex}.upload")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        size = 0
        try:
            with open(tmp, "wb") as out:  # streamed to disk in 1 MB pieces
                while True:
                    piece = await file.read(1024 * 1024)
                    if not piece:
                        break
                    size += len(piece)
                    if size > MAX_ASSET_BYTES:
                        raise HTTPException(status_code=413, detail="The file is larger than the library accepts.")
                    out.write(piece)
            try:
                asset, created = library.register_file(db, tmp, owner_id=user.id, source="upload",
                                                       file_name=file.filename, move=True)
            except MediaError as e:
                raise HTTPException(status_code=400, detail=f"This file cannot be added to the library: {e}.")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        counts = library.reference_counts(db, [asset.id])
        return {"asset": serialize(asset, user.id, counts.get(asset.id, 0)), "deduplicated": not created}

    @router.get("")
    def list_assets(kind: str | None = None, source: str | None = None, scope: str | None = None, q: str | None = None,
                    status: str | None = "ready", limit: int = 100, offset: int = 0,
                    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        query = db.query(models.Asset).filter(models.Asset.status != "deleted")
        if scope == "system":
            query = query.filter(models.Asset.owner_id.is_(None))
        elif scope == "mine":
            query = query.filter(models.Asset.owner_id == user.id)
        else:
            query = query.filter((models.Asset.owner_id == user.id) | (models.Asset.owner_id.is_(None)))
        if kind in KINDS:
            query = query.filter(models.Asset.kind == kind)
        if source in SOURCES:
            query = query.filter(models.Asset.source == source)
        if status in STATUSES:
            query = query.filter(models.Asset.status == status)
        if q:
            # The file name, or the words describing it (description, keywords, a generated file's prompt)
            pattern = f"%{q.strip()[:80]}%"
            query = query.filter(models.Asset.file_name.ilike(pattern) | models.Asset.details.ilike(pattern))
        total = query.count()
        assets = query.order_by(models.Asset.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
        counts = library.reference_counts(db, [a.id for a in assets])
        return {"assets": [serialize(a, user.id, counts.get(a.id, 0)) for a in assets], "total": total}

    @router.post("/resolve")
    def resolve_assets(body: ResolveRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        wanted = list(dict.fromkeys(body.ids[:500]))
        valid = [i for i in wanted if isinstance(i, str) and re.fullmatch(r"[0-9a-f]{32}", i)]
        rows = {a.id: a for a in db.query(models.Asset).filter(models.Asset.id.in_(valid))} if valid else {}
        resolved, missing, went_missing = {}, [], False
        for asset_id in wanted:  # one query for all; a file check (stat) per asset; one commit at most
            asset = rows.get(asset_id)
            if not library.usable_by(asset, user.id):
                missing.append(asset_id)
                continue
            if asset.status == "ready" and not library.file_exists(asset):
                asset.status, asset.error_message = "failed", "The file is missing from storage."
                went_missing = True
            if asset.status != "ready":
                missing.append(asset_id)
                continue
            resolved[asset_id] = {**serialize(asset, user.id), "url": content_url(asset, user)}
        if went_missing:
            db.commit()
        return {"assets": resolved, "missing": missing}

    @router.get("/{asset_id}")
    def get_asset(asset_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        asset = get_or_404(db, asset_id, user)
        rows = db.query(models.AssetReference, models.Project).join(
            models.Project, models.Project.id == models.AssetReference.project_id).filter(
            models.AssetReference.asset_id == asset.id).all()
        # Lessons are listed only when they are yours; shared assets just show how often they are used
        used_in = [{"project_id": project.id, "field": ref.field,
                    "title": " ".join(filter(None, [project.subject_name, project.session_number, project.session_title]))}
                   for ref, project in rows if project.user_id == user.id]
        return {**serialize(asset, user.id, len(rows)), "used_in": used_in}

    @router.patch("/{asset_id}")
    def update_asset(asset_id: str, body: AssetUpdate, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        """Sets the description and keywords of your own asset (used to match it to lesson visuals).
        A field left out is unchanged; an empty one is cleared. Other details are kept."""
        asset = get_or_404(db, asset_id, user)  # deleted assets and other users' assets: 404
        if asset.owner_id is None:
            raise HTTPException(status_code=403, detail="Shared system assets cannot be edited.")
        details = json.loads(asset.details) if asset.details else {}
        if body.description is not None:
            description = clean_text(body.description)
            if len(description) > DESCRIPTION_MAX:
                raise HTTPException(status_code=422, detail=f"Keep the description to {DESCRIPTION_MAX} characters or fewer.")
            if description:
                details["description"] = description
            else:
                details.pop("description", None)
        if body.keywords is not None:
            try:
                keywords = clean_keywords(body.keywords)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            if keywords:
                details["keywords"] = keywords
            else:
                details.pop("keywords", None)
        asset.details = json.dumps(details) if details else None
        db.commit()
        db.refresh(asset)
        return serialize(asset, user.id, library.reference_counts(db, [asset.id]).get(asset.id, 0))

    @router.delete("/{asset_id}")
    def delete_asset(asset_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        asset = get_or_404(db, asset_id, user)
        if asset.owner_id is None:
            raise HTTPException(status_code=403, detail="Shared system assets cannot be deleted.")
        uses = library.reference_counts(db, [asset.id]).get(asset.id, 0)
        if uses:
            raise HTTPException(status_code=409, detail={
                "message": f"This asset is used by {uses} saved lesson item(s), so it was kept.", "references": uses})
        file_removed = library.delete(db, asset)
        return {"deleted": True, "file_removed": file_removed}

    @router.get("/{asset_id}/content")
    def asset_content(asset_id: str, request: Request, token: str | None = None, db: Session = Depends(get_db)):
        user = authenticate_media_request(request, token, scope="asset", resource_id=asset_id, db=db,
                                          secret=secret_key, algorithm=algorithm, noun="asset")
        asset = get_or_404(db, asset_id, user)
        if asset.status != "ready":
            raise HTTPException(status_code=409, detail="This asset is not ready.")
        if not library.file_exists(asset):
            library.mark_missing(db, asset)
            raise HTTPException(status_code=410, detail="The asset's file is missing from storage.")
        return FileResponse(library.file_path(asset), media_type=asset.mime_type, filename=asset.file_name,
                            content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})

    return router


# Files shipped with the app that every lesson uses: Aadhi's clips and posters, and the intro media
SYSTEM_ASSETS = [
    ("aadhi_left.mp4", "mascot", {"placement": "left"}),
    ("aadhi_right.mp4", "mascot", {"placement": "right"}),
    ("aadhi_center.mp4", "mascot", {"placement": "center"}),
    ("aadhi_popup.mp4", "mascot", {"placement": "popup"}),
    ("no_aadhi.mp4", "mascot", {"placement": "hidden"}),
    ("posters/aadhi_left.jpg", "mascot", {"placement": "left", "role": "poster"}),
    ("posters/aadhi_right.jpg", "mascot", {"placement": "right", "role": "poster"}),
    ("posters/aadhi_center.jpg", "mascot", {"placement": "center", "role": "poster"}),
    ("posters/aadhi_popup.jpg", "mascot", {"placement": "popup", "role": "poster"}),
    ("posters/no_aadhi.jpg", "mascot", {"placement": "hidden", "role": "poster"}),
    ("static_background.png", "system", {"role": "background"}),
    ("logo_animation.mp4", "system", {"role": "intro"}),
    ("bgm.mp3", "system", {"role": "music"}),
]


def register_system_assets(library, db):
    """Registers the shipped media as shared system assets. Cheap after the first start: files
    whose size is unchanged are not read again."""
    registered = 0
    for key, source, details in SYSTEM_ASSETS:
        try:
            library.adopt(db, "system", key, owner_id=None, source=source, details=details,
                          file_name=key.split("/")[-1])
            registered += 1
        except (MediaError, StorageError) as e:
            print(f"[ASSETS] System asset {key} not registered: {e}")
    return registered


def build_library(static_dir, system_dir):
    return AssetLibrary({
        "assets": LocalStorage(ASSETS_DIR),
        "static": LocalStorage(static_dir),
        "system": LocalStorage(system_dir),
    })
