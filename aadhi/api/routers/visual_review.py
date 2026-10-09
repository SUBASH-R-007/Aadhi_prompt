"""Visual Review (``aadhi.review``): every scene's visual of a version, the teacher's sign-off and the visual actions.

* ``GET /api/versions/{vid}/visual-review``: ``{"summary", "scenes": [SceneVisual], "revision"}`` (``revision``:
  the screenplay revision the scenes were read at, for the actions). Reads only; never generates or builds
  anything. Media URLs are served like timeline URLs (stable public URLs, or presigned at
  serve time on S3 without a CDN).
* ``PUT /api/versions/{vid}/visual-review/{scene_id}``: approve, or back to pending (``{"state", "note"?,
  "revision"?}``). An approval sent with the ``revision`` the teacher saw is refused (409 ``revision_conflict``)
  when the screenplay changed since, so a sign-off never covers a request nobody looked at.
* ``POST /api/versions/{vid}/scenes/{scene_id}/visual``: ``new_version`` | ``choose_library`` | ``remove`` |
  ``retry`` on the version at ``revision`` (409 ``revision_conflict`` otherwise, like the editor's save).
  ``new_version`` and ``retry`` build that scene (budget pre-check and the hourly generation limit, like
  ``POST .../build``); a scene whose AI video may already have been billed needs ``confirm_paid: true`` (409
  ``confirm_paid_required``). ``new_version`` only while that medium can be generated for the lecture with the
  requester's keys, and ``retry`` only when a rebuild can change the visual; neither for a scene skipped in the
  video (``hidden``: the build leaves it out) (409 ``action_unavailable`` otherwise, before anything is charged). ``choose_library`` takes the requester's own library item only (404
  otherwise), and only in the requester's own lecture (403 ``forbidden`` for an admin acting on someone else's).

Access: ``load_version`` (owner or admin, else 404). Library items are always the requester's own. Every
response scores the requester's library against the scenes (bounded: ``library.MATCH_MAX_PAIRS``; one scene's
for the writes), so reads and writes have per-user limits on top of the API's.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import ConfigDict, Field

from ... import review
from ...models import Project, ProjectVersion
from ...pipeline.assets import CONFIRM_PAID_KEY
from ...schemas.jsonsafe import json_safe
from ...schemas.manifest import MediaInfo
from ...schemas.screenplay import Screenplay
from ...security.ratelimit import check_rate_limit
from ...storage.base import is_public_key
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, load_version
from ..errors import ApiException, not_found
from ..generation import (
    charge_generation_rate,
    commit_and_notify,
    enqueue_job,
    ensure_budget,
    ensure_not_busy,
    request_keys,
    version_options,
)
from ..screenplay_service import authorize_asset_keys, stale_scenes
from ..storable import StorableBody
from ..timelines import signed_urls
from .versions import safe_manifest, safe_screenplay

router = APIRouter(prefix="/api/versions", tags=["visual review"], dependencies=RATE_LIMITED)

# Reading the review matches every scene against the requester's library: a per-user limit on top of the API's.
REVIEW_READS_PER_MINUTE = 60
# Each sign-off or action answers with the scene's view (its library matches included): limited per user too.
REVIEW_WRITES_PER_MINUTE = 60


class ReviewStateBody(StorableBody):
    """The teacher's sign-off of one scene's visual (``pending`` resets it). ``revision``: the screenplay revision
    the teacher saw (``GET .revision``); an approval of an older one is refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    state: Literal["approved", "pending"]
    note: str | None = Field(default=None, max_length=review.MAX_NOTE)
    revision: int | None = Field(default=None, ge=1)


class VisualActionBody(StorableBody):
    """One action on a scene's visual, based on the screenplay ``revision`` the teacher saw."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["new_version", "choose_library", "remove", "retry"]
    library_item_id: int | None = Field(default=None, ge=1, le=2**63 - 1)
    confirm_paid: bool = False
    revision: int = Field(ge=1)


def _require_screenplay(version: ProjectVersion) -> Screenplay:
    sp = safe_screenplay(version)
    if sp is None:
        raise ApiException(409, "no_screenplay", "This version has no screenplay yet.")
    return sp


def _scene(sp: Screenplay, scene_id: str) -> tuple[int, Any]:
    for i, scene in enumerate(sp.scenes):
        if scene.id == scene_id:
            return i, scene
    raise not_found("Scene")


def _url_signer(store: Any, settings: Any) -> Any:
    """``MediaInfo -> URL | None``, exactly as timeline URLs are served (public namespace only)."""
    signed = signed_urls(settings)
    ttl = int(settings.s3_presign_ttl_seconds)

    def url_for(info: MediaInfo) -> str | None:
        key = info.storage_key
        if not key or not is_public_key(key):
            return None
        try:
            return store.storage.signed_url(key, ttl) if signed else store.url_for(key)
        except Exception:  # noqa: BLE001 - a preview link only
            return None

    return url_for


def _context(db: Any, version: ProjectVersion, sp: Screenplay, user: Any, settings: Any) -> review.ReviewContext:
    """The review of ``version`` as ``user`` sees it (what they can generate with their own keys included)."""
    manifest = safe_manifest(version)
    project = db.get(Project, version.project_id)
    generate = review.generation_available(version_options(version, project), request_keys(db, user, settings).settings)
    return review.load_context(db, version, sp, manifest, stale_scenes(sp, manifest), user.id, generate=generate)


def _one(db: Any, version: ProjectVersion, user: Any, scene_id: str, settings: Any, store: Any) -> dict[str, Any]:
    sp = _require_screenplay(version)
    index, scene = _scene(sp, scene_id)
    ctx = _context(db, version, sp, user, settings)
    item = review.scene_view(ctx, index, scene, url_for=_url_signer(store, settings))
    counts = review.suggestion_counts(db, user.id, sp, scene_ids={scene.id},  # last: see its doc
                                      shown=review.shown_keys(sp, ctx.manifest))
    item["library_suggestions"] = counts.get(scene.id, 0)
    return json_safe(item)


@router.get("/{vid}/visual-review")
def get_visual_review(vid: int, user: CurrentUser, db: DbSession, settings: AppSettings, store: Store) -> dict[str, Any]:
    """Every scene's visual with its source, status, sign-off and actions, plus summary counts."""
    version = load_version(db, user, vid)
    check_rate_limit("visual_review", f"user:{user.id}", per_minute=REVIEW_READS_PER_MINUTE)
    sp = _require_screenplay(version)
    ctx = _context(db, version, sp, user, settings)
    url_for = _url_signer(store, settings)
    items = [review.scene_view(ctx, i, scene, url_for=url_for) for i, scene in enumerate(sp.scenes)]
    counts = review.suggestion_counts(db, user.id, sp, shown=review.shown_keys(sp, ctx.manifest))  # last: its doc
    for item in items:
        item["library_suggestions"] = counts.get(item["scene_id"], 0)
    return json_safe({"summary": review.summary(items), "scenes": items, "revision": version.revision})


@router.put("/{vid}/visual-review/{scene_id}")
def put_visual_review(
    vid: int, scene_id: str, body: ReviewStateBody, user: CurrentUser, db: DbSession, settings: AppSettings,
    store: Store,
) -> dict[str, Any]:
    """Approve the scene's visual as it is asked for now, or reset the sign-off to pending."""
    version = load_version(db, user, vid)
    check_rate_limit("visual_review_write", f"user:{user.id}", per_minute=REVIEW_WRITES_PER_MINUTE)
    if body.state == "approved" and body.revision is not None and body.revision != version.revision:
        raise ApiException(409, "revision_conflict", "The screenplay was changed elsewhere.",
                           current_revision=version.revision)
    sp = _require_screenplay(version)
    _, scene = _scene(sp, scene_id)
    slot = review.visual_slot(sp, scene)
    if body.state == "approved" and slot.kind == "none":
        raise ApiException(409, "no_visual", "This scene has no visual to approve.")
    note: Any = (body.note or None) if "note" in body.model_fields_set else review.UNSET
    review.set_review(db, version.id, scene.id, body.state, review.fingerprint(slot), user.id, note)
    db.commit()
    return _one(db, version, user, scene.id, settings, store)


@router.post("/{vid}/scenes/{scene_id}/visual")
def visual_action(
    vid: int, scene_id: str, body: VisualActionBody, user: CurrentUser, db: DbSession, settings: AppSettings,
    store: Store,
) -> dict[str, Any]:
    """Make a new AI version, use a library item, remove the visual, or build the scene again."""
    version = load_version(db, user, vid)
    check_rate_limit("visual_review_write", f"user:{user.id}", per_minute=REVIEW_WRITES_PER_MINUTE)
    sp = _require_screenplay(version)
    _, scene = _scene(sp, scene_id)
    if body.revision != version.revision:
        raise ApiException(409, "revision_conflict", "The screenplay was changed elsewhere.",
                           current_revision=version.revision)
    ensure_not_busy(db, version.id)
    project = db.get(Project, version.project_id)
    paid = body.action in ("new_version", "retry")
    if paid and scene.hidden:  # the build skips a hidden scene: nothing would be made, nothing is charged
        raise ApiException(409, "action_unavailable",
                           "This scene is skipped in the video. Show it again in the editor to build its visual.")
    slot = review.visual_slot(sp, scene)
    if body.action == "retry" and slot.kind == "none":
        raise ApiException(409, "action_unavailable", "This scene has no visual to build.")
    confirm = paid and review.ambiguous(version, scene.id)
    if confirm and not body.confirm_paid:
        raise ApiException(
            409, "confirm_paid_required",
            "An earlier attempt to make this AI video may already have been billed. Confirm that it may be billed "
            "again to make it now.")
    ctx = _context(db, version, sp, user, settings) if paid else None
    if ctx is not None and body.action == "retry" and not confirm:
        media = ctx.media(scene.id)
        status, _ = review.visual_status(slot, media, ctx.issues.get(scene.id, []), scene.id in ctx.stale)
        if review.settled(media) and status in ("fallback", "missing"):  # a stale page: spends no token
            raise ApiException(409, "action_unavailable",
                               "Building this scene again cannot change its visual: choose or upload one instead.")
    edited = _edited_scene(db, body, scene, project, user.id, ctx) if body.action != "retry" else None
    if paid:
        ensure_budget(db, user, settings, keys=request_keys(db, user, settings), options=version_options(version, project))
    now = dt.datetime.now(dt.timezone.utc)
    revision = version.revision
    if edited is not None:
        new_scene, state = edited
        new_sp = review.with_scene(sp, scene.id, new_scene)
        authorize_asset_keys(db, version.project_id, new_sp)
        revision = review.write_screenplay(db, version, new_sp, revision=body.revision, project=project, store=store,
                                           now=now)
        review.set_review(db, version.id, scene.id, state, review.fingerprint(review.visual_slot(new_sp, new_scene)),
                          user.id)
    job_id: int | None = None
    if paid:
        payload: dict[str, Any] = {"scene_ids": [scene.id], "base_revision": revision}
        if confirm:
            payload[CONFIRM_PAID_KEY] = [scene.id]
        job = enqueue_job(db, "build_assets", user=user, project_id=version.project_id, version_id=version.id,
                          payload=payload)
        charge_generation_rate(db, user, settings)
        job_id = job.id
        commit_and_notify(db, settings)
    else:
        db.commit()
    db.expire(version)  # the screenplay was written with a Core UPDATE: reload it
    return {"revision": version.revision, "scene": _one(db, version, user, scene.id, settings, store),
            "job_id": job_id}


def _edited_scene(db: Any, body: VisualActionBody, scene: Any, project: Project | None, user_id: int,
                  ctx: review.ReviewContext | None) -> tuple[Any, str]:
    """(edited scene, sign-off state) of a screenplay-changing action."""
    if body.action == "new_version":
        if ctx is None:  # pragma: no cover - new_version is a paid action: the context is loaded
            raise ApiException(409, "action_unavailable", "A new version cannot be made now.")
        return review.new_version_scene(scene, media=ctx.media(scene.id), generate=ctx.generate), "changed"
    if body.action == "remove":
        return review.removed_scene(scene), review.removed_state(scene)
    if body.library_item_id is None:
        raise ApiException(422, "validation", [{"loc": ["body", "library_item_id"],
                                                "msg": "Choose a library item.", "type": "missing"}])
    if not review.can_take_picture(scene):
        raise ApiException(409, "action_unavailable", "This scene cannot show a picture or a video.")
    if project is None:  # pragma: no cover - load_version joined the project
        raise not_found("Project")
    from ... import library

    item = library.get_item(db, user_id, body.library_item_id)  # the requester's own item, else 404
    if project.owner_id != user_id:  # like POST /api/library/{id}/attach: only into the requester's own lectures
        raise ApiException(403, "forbidden", "Pictures and videos from your library can be used only in your own "
                                             "lectures.")
    review.check_pick(scene, str(item.kind))  # before anything is attached to the project
    key = library.attach_to_project(db, item, project)
    db.flush()
    return review.chosen_scene(scene, key, str(item.kind), title=str(getattr(item, "title", "") or "")), "changed"
