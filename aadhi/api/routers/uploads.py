"""``POST /api/uploads``: teacher media (scene media, figures, side panels, posters)."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import library
from ...models import AssetRef
from ...security.uploads import validate_upload
from ...storage.assets import Produced, bytes_key
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, load_project
from ..errors import ApiException
from ..upload_io import MediaProbe, probe_image, probe_video, read_upload

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"], dependencies=RATE_LIMITED)

Purpose = Literal["scene_media", "figure", "side_panel", "poster"]

PURPOSE_KINDS: dict[str, set[str]] = {
    "scene_media": {"image", "video"},
    "figure": {"image"},
    "side_panel": {"image", "video"},
    "poster": {"image"},
}
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif", "video/mp4", "video/webm"}


@router.post("", status_code=201)
def upload_media(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    store: Store,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[Purpose, Form()],
    project_id: Annotated[int, Form()],
) -> dict[str, Any]:
    """Store an image/video for a project the user owns and record the asset reference."""
    project = load_project(db, user, project_id)
    max_bytes = settings.upload_max_mb * 1024 * 1024
    data = read_upload(file, max_bytes)
    info = validate_upload(file.filename or "upload", data, allowed_kinds=PURPOSE_KINDS[purpose], max_bytes=max_bytes)
    if info.mime not in ALLOWED_MIMES or info.kind not in PURPOSE_KINDS[purpose]:
        raise ApiException(415, "unsupported_type", "Allowed: png, jpg, webp, gif, mp4, webm.")
    if info.kind == "image":
        width, height = getattr(info, "width", None), getattr(info, "height", None)
        probe = MediaProbe(width=width, height=height) if width and height else probe_image(data)
    elif info.kind == "video":
        probe = probe_video(data, info.mime, settings)
    else:  # pragma: no cover - excluded by PURPOSE_KINDS
        probe = MediaProbe()
    db.commit()  # end any read transaction before the asset store writes in its own session
    key = bytes_key("upload", data)
    asset = store.put(
        key,
        "upload",
        Produced(
            data=data,
            mime=info.mime,
            width=probe.width,
            height=probe.height,
            duration_s=probe.duration_s,
            meta={"purpose": purpose},
        ),
        created_by=user.id,
    )
    exists = db.execute(
        select(AssetRef.id).where(AssetRef.project_id == project.id, AssetRef.asset_key == asset.key)
    ).scalar_one_or_none()
    if exists is None:
        db.add(AssetRef(project_id=project.id, asset_key=asset.key))
        try:
            db.commit()
        except IntegrityError:  # a concurrent upload of the same bytes recorded it first
            db.rollback()
    _add_to_library(db, user.id, asset.key, info.kind, file.filename, project.id)
    out: dict[str, Any] = {
        "asset_key": asset.key,
        "url": store.url_for(asset.storage_key),
        "mime": asset.mime,
        "kind": info.kind,
    }
    width = asset.width if asset.width is not None else probe.width
    height = asset.height if asset.height is not None else probe.height
    duration = asset.duration_s if asset.duration_s is not None else probe.duration_s
    if width is not None:
        out["width"] = width
    if height is not None:
        out["height"] = height
    if duration is not None:
        out["duration_s"] = duration
    return out


def _add_to_library(db: Session, user_id: int, asset_key: str, kind: str, filename: str | None, project_id: int) -> None:
    """The upload also joins the uploader's own media library (``aadhi.library``; titled from the file name,
    words the teacher gave the same file earlier are kept). Never fails the upload."""
    if kind not in library.KINDS:
        return
    try:
        library.add_item(db, user_id=user_id, asset_key=asset_key, kind=kind, source="upload",
                         title=library.title_from_filename(filename, kind), origin_project_id=project_id, used=True)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the library is a convenience: the upload itself succeeded
        db.rollback()
        log.warning("uploads: could not add %s to the uploader's library: %s", asset_key, type(exc).__name__)
