"""``/api/library``: each teacher's own media library (``aadhi.library``).

* Every route is the signed-in user's own library: items of other users are 404, admins included (an
  admin's library is their own). Mutations go through the CSRF middleware like every cookie request.
* ``GET /api/library`` lists items (``q`` / ``kind`` / ``source``, ``limit`` / ``offset``); ``POST`` adds a
  picture or clip without a lecture (the same checks and limits as ``POST /api/uploads``); ``PATCH`` edits
  the title, description and keywords; ``DELETE`` removes the item only (lectures that use the media keep
  it; the asset GC is unchanged).
* ``POST /api/library/{id}/attach`` makes the media usable in one of the user's own lectures (an
  ``AssetRef``); the editor then sets the key as an override as for an upload.
* ``GET /api/library/suggestions?version_id=`` matches the user's items to the scenes of a version
  (deterministic, rate limited); ``POST /api/library/{id}/describe`` asks the AI engine for a title,
  description and keywords (``LIBRARY_AI_DESCRIBE_ENABLED``; rate limited, billed and budgeted like every
  LLM call, using the user's own key when they saved one).
* ``GET /api/admin/media-cache`` (admins): aggregate counts of generated media made, billed and reused.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import library, review
from ...config import Settings
from ...credentials import ResolvedKeys, billed_to, personal_key_rejection, resolve_keys
from ...jobs.base import BudgetExceeded
from ...models import Asset, LibraryItem, User
from ...pipeline import integrations
from ...providers.base import ImageInput, LLMProvider, ProviderNotConfigured, Usage
from ...security.ratelimit import check_rate_limit
from ...security.uploads import validate_upload
from ...storage.assets import AssetStore, Produced, bytes_key
from ...usage.service import check_budget, record_usage
from ..deps import RATE_LIMITED, AdminUser, AppSettings, CurrentUser, DbSession, Store, load_project, load_version
from ..errors import ApiException, not_found, validation_error
from ..storable import StorableBody
from ..upload_io import MediaProbe, probe_image, probe_video, read_upload
from ..util import page_params
from .uploads import ALLOWED_MIMES
from .versions import safe_manifest, safe_screenplay

log = logging.getLogger(__name__)

router = APIRouter(tags=["library"], dependencies=RATE_LIMITED)

DEFAULT_PAGE = 48
SUGGESTIONS_PER_MINUTE = 30  # each one reads the version's screenplay and scores the library
DESCRIBE_PER_MINUTE = 6  # each one is a paid model call
DESCRIBE_ESTIMATE_USD = 0.01  # the budget pre-check: refused when the daily budget has no room for this


def _bad(field: str, exc: ValueError) -> ApiException:
    return validation_error(str(exc), ["body", field])


def _views(db: Session, store: AssetStore, user: User, items: list[LibraryItem]) -> list[dict[str, Any]]:
    return library.item_views(db, store, user.id, items)


# --- browse ----------------------------------------------------------------------------------------


@router.get("/api/library")
def list_library(
    user: CurrentUser,
    db: DbSession,
    store: Store,
    q: str = "",
    kind: str = "",
    source: str = "",
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """The user's pictures and clips, newest first (``q``: words of the title, keywords, description or
    prompt; ``kind``: image | video; ``source``: upload | generated | figure, or several comma-separated)."""
    limit, offset = page_params(limit, offset)
    if kind and kind not in library.KINDS:
        raise validation_error(f"kind must be one of {', '.join(library.KINDS)}", ["query", "kind"])
    wanted = [s for s in source.split(",") if s]
    if any(s not in library.SOURCES for s in wanted):
        raise validation_error(f"source must be one or more of {', '.join(library.SOURCES)}", ["query", "source"])
    rows, total = library.list_items(
        db, user.id, q=q, kind=kind or None, source=tuple(wanted) or None, limit=limit, offset=offset
    )
    uses = library.used_in(db, user.id, (item.asset_key for item, _ in rows))
    items = [library.item_view(item, asset, store, uses.get(item.asset_key, 0)) for item, asset in rows]
    return {"items": items, "total": total}


# --- add (upload without a lecture) ----------------------------------------------------------------


@router.post("/api/library", status_code=201)
def upload_to_library(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    store: Store,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    keywords: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Add a picture or clip to the library (same validation and limits as ``POST /api/uploads``). Words
    typed here apply to the item even when the same file is already in the library; empty fields keep what
    the item has."""
    typed: dict[str, Any] = {}
    for field, value, clean in (
        ("title", title, library.clean_title),
        ("description", description, library.clean_description),
        ("keywords", keywords, library.clean_keywords),
    ):
        try:
            cleaned = clean(value) if value is not None else None
        except ValueError as exc:
            raise _bad(field, exc) from None
        if cleaned:
            typed[field] = cleaned
    max_bytes = settings.upload_max_mb * 1024 * 1024
    data = read_upload(file, max_bytes)
    info = validate_upload(file.filename or "upload", data, allowed_kinds=set(library.KINDS), max_bytes=max_bytes)
    if info.mime not in ALLOWED_MIMES or info.kind not in library.KINDS:
        raise ApiException(415, "unsupported_type", "Allowed: png, jpg, webp, gif, mp4, webm.")
    if info.kind == "image":
        width, height = getattr(info, "width", None), getattr(info, "height", None)
        probe = MediaProbe(width=width, height=height) if width and height else probe_image(data)
    else:
        probe = probe_video(data, info.mime, settings)
    db.commit()  # end any read transaction before the asset store writes in its own session
    asset = store.put(
        bytes_key("upload", data),
        "upload",
        Produced(
            data=data,
            mime=info.mime,
            width=probe.width,
            height=probe.height,
            duration_s=probe.duration_s,
            meta={"purpose": "library"},
        ),
        created_by=user.id,
    )
    item = library.add_item(
        db,
        user_id=user.id,
        asset_key=asset.key,
        kind=info.kind,
        source="upload",
        title=typed.get("title") or library.title_from_filename(file.filename, info.kind),
    )
    item = library.update_item(db, item, **typed)  # the teacher's words win, also for a file already kept
    db.commit()
    return library.item_view(item, asset, store, library.used_in(db, user.id, [item.asset_key]).get(item.asset_key, 0))


# --- edit / delete ---------------------------------------------------------------------------------


class LibraryPatch(StorableBody):
    """The teacher's words for one item (absent fields are kept; an empty description or keyword list
    clears it; the title cannot be empty)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=10_000)
    keywords: list[str] | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        title = library.clean_title(v)
        if not title:
            raise ValueError("The title cannot be empty.")
        return title

    @field_validator("description")
    @classmethod
    def _description(cls, v: str | None) -> str | None:
        return None if v is None else library.clean_description(v)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else library.clean_keywords(v)


@router.patch("/api/library/{item_id}")
def patch_item(item_id: int, body: LibraryPatch, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """Edit the item's title, description or keywords."""
    item = library.get_item(db, user.id, item_id)
    changes = {f: getattr(body, f) for f in ("title", "description", "keywords") if f in body.model_fields_set}
    if changes.get("title", "") is None:
        changes.pop("title")
    item = library.update_item(db, item, **changes)
    db.commit()
    return _views(db, store, user, [item])[0]


@router.delete("/api/library/{item_id}", status_code=204)
def delete_item(item_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Remove the item from the library (lectures that use its media keep it)."""
    item = library.get_item(db, user.id, item_id)
    library.delete_item(db, item)
    db.commit()
    return Response(status_code=204)


# --- use in a lecture ------------------------------------------------------------------------------


class AttachBody(StorableBody):
    model_config = ConfigDict(extra="forbid")

    project_id: int


@router.post("/api/library/{item_id}/attach")
def attach_item(item_id: int, body: AttachBody, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Make the item's media usable in one of the user's own lectures; returns the key to use as an
    ``override_asset_key`` (or a figure's ``asset_key``) in its screenplay."""
    item = library.get_item(db, user.id, item_id)
    project = load_project(db, user, body.project_id)
    if project.owner_id != user.id:  # the user's own lectures only (load_project also admits admins)
        raise not_found("Project")
    key = library.attach_to_project(db, item, project)
    db.commit()
    return {"asset_key": key}


# --- suggestions -----------------------------------------------------------------------------------


@router.get("/api/library/suggestions")
def library_suggestions(version_id: int, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """For each scene of the version that shows a generated picture or clip and has none chosen yet: the
    user's best matching items (at most 3, score >= 0.35, best first), never the media the scene shows now.
    A version without a readable screenplay has none."""
    version = load_version(db, user, version_id)
    check_rate_limit("library_suggestions", f"user:{user.id}", per_minute=SUGGESTIONS_PER_MINUTE)
    sp = safe_screenplay(version)
    if sp is None:
        return {"scenes": []}
    found = library.suggestions(db, user.id, sp, shown=review.shown_keys(sp, safe_manifest(version)))
    unique = list({item.id: item for _, ranked in found for item, _ in ranked}.values())
    views = {view["id"]: view for view in _views(db, store, user, unique)}
    return {
        "scenes": [
            {
                "scene_id": need.scene_id,
                "matches": [{"item": views[item.id], "score": value} for item, value in ranked],
            }
            for need, ranked in found
        ]
    }


# --- AI description suggestion ---------------------------------------------------------------------


@dataclass
class _DescribeCall:
    item: LibraryItem
    keys: ResolvedKeys
    llm: LLMProvider
    model: str
    image: ImageInput | None


def _prepare_describe(db: Session, settings: Settings, store: AssetStore, user: User, item_id: int) -> _DescribeCall:
    """Authorise, rate limit, check the budget and read the media (blocking)."""
    item = library.get_item(db, user.id, item_id)
    check_rate_limit("library_describe", f"user:{user.id}", per_minute=DESCRIBE_PER_MINUTE)
    keys = resolve_keys(db, settings, user.id)
    engine = settings.llm_provider
    try:
        llm = integrations.get_llm(keys.settings, engine)
        model = integrations.llm_model(keys.settings, "fast")
    except ProviderNotConfigured:
        raise ApiException(503, "unavailable", "No AI engine is configured for you.") from None
    if keys.source(engine) != "personal":  # spend on the user's own key never uses up the daily budget
        check_budget(db, user_id=user.id, job_cost_so_far=0.0, extra_usd=DESCRIBE_ESTIMATE_USD, settings=settings)
    asset = db.execute(select(Asset).where(Asset.key == item.asset_key)).scalar_one_or_none()
    db.commit()  # no transaction stays open during the model call
    image = library.describe_image(store, asset, settings)
    return _DescribeCall(item=item, keys=keys, llm=llm, model=model, image=image)


def _record_describe_usage(db: Session, settings: Settings, user: User, keys: ResolvedKeys, usages: list[Usage]) -> None:
    """Store the usage of the model call(s) (priced; billed to the user's own key when it paid)."""
    from ...jobs.context import price_usage, sanitize_usage

    try:
        for usage in usages:
            clean = sanitize_usage(usage, settings.redact)
            clean.meta = {**(clean.meta or {}), "purpose": "library_describe"}
            record_usage(db, clean, user_id=user.id, project_id=None, job_id=None,
                         cost_usd=price_usage(clean, settings), billed_to=billed_to(keys.sources, clean.provider))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - never hide the answer because the bookkeeping failed
        db.rollback()
        log.warning("library: could not record describe usage: %s", type(exc).__name__)


@router.post("/api/library/{item_id}/describe")
async def describe_item(
    item_id: int, user: CurrentUser, db: DbSession, settings: AppSettings, store: Store
) -> dict[str, Any]:
    """A suggested title, description and keywords for the item (not saved: the teacher reviews them and
    saves with ``PATCH``). 403 ``feature_disabled`` unless ``LIBRARY_AI_DESCRIBE_ENABLED``."""
    if not settings.library_ai_describe_enabled:
        raise ApiException(403, "feature_disabled", "AI descriptions are switched off on this server.")
    call = await asyncio.to_thread(_prepare_describe, db, settings, store, user, item_id)
    usages: list[Usage] = []
    try:
        async with integrations.limit("llm"):
            return await library.describe(
                call.llm, model=call.model, item=call.item, image=call.image, on_usage=usages.append
            )
    except (ApiException, BudgetExceeded):
        raise
    except ProviderNotConfigured:
        raise ApiException(503, "unavailable", "No AI engine is configured for you.") from None
    except Exception as exc:  # noqa: BLE001 - any provider failure is one clear answer
        log.info("library: describe failed: %s", settings.redact(str(exc))[:300])
        message = personal_key_rejection(exc, call.keys.sources)
        raise ApiException(
            502, "describe_failed", message or "The AI engine could not describe this item. Try again later."
        ) from None
    finally:
        if usages:
            await asyncio.to_thread(_record_describe_usage, db, settings, user, call.keys, usages)


# --- admin: media reuse statistics -----------------------------------------------------------------


@router.get("/api/admin/media-cache")
def media_cache(admin: AdminUser, db: DbSession, days: int = 30) -> dict[str, Any]:
    """Admins: generated pictures and clips made, billed and reused from other lectures in the last ``days``
    days (1..366), media stored, and library sizes. Aggregates only: no prompts, no users."""
    if days < 1 or days > 366:
        raise validation_error("days must be 1..366", ["query", "days"])
    return library.media_cache_stats(db, days=days)
