"""Visual Review: every scene's visual in one place, the teacher's sign-off on it and the visual actions.

Read side (``scene_visuals``): per scene of a version, what its visual is (``visual_slot``: the side panel, or
the main media of simulation / AI video / interactive scenes), where it came from (``source``: generated,
library, upload, figure, manim, builtin, fallback or none), whether it is usable (``status``: ready, missing,
failed, fallback, ambiguous or stale) with a plain-words reason, the sign-off (``VisualReview`` rows) and the
actions that apply. Built from the stored screenplay, asset manifest and issues: opening the review never
generates or builds anything.

Sign-off: ``pending`` (no row), ``approved``, ``changed`` (the teacher replaced the visual here) or ``removed``.
Each row stores the ``fingerprint`` of what the scene asked its visual to show (the visual request without
narration, title or rationale, plus the chosen asset key and the variant). When the request changes later, the
row is ``stale``: an approval then reads as pending again, a change or removal is kept and flagged. Rows live
outside the screenplay, so approving never changes a scene hash or a build cache.

Write side: ``set_review`` (approve / reset) and the screenplay edits of the actions, which the router saves
with the same revision check as the editor (``write_screenplay``):

* ``new_version``: ``variant`` + 1 on the generated image panel or the AI video scene (its own content key in
  ``aadhi.pipeline.assets``, so the earlier asset stays cached); the router then builds that scene. Offered and
  accepted only while that medium can be generated for the lecture with the requester's keys
  (``generation_available``); a build that still cannot make it keeps showing the earlier version.
* ``choose_library``: the requester's own library item (``aadhi.library``) is attached to the project (the
  requester's own lecture only, like ``POST /api/library/{id}/attach``) and set as the scene's override (side
  panel, simulation or AI video media, interactive poster).
* ``remove``: the side panel is removed; for main media the teacher's override is removed (the scene's own
  animation / clip / still comes back). A scene's own main media cannot be removed.

A scene the teacher skipped in the video (``hidden``) is still listed (``hidden: true``) and can be approved,
replaced or removed, but the build skips it, so nothing that builds it is offered (``new_version``, ``retry``,
``confirm_paid_retry``) and it never counts as needing attention.

Ownership never comes from content-addressed ``Asset`` rows: library items are looked up by the requester's
user id, and the project's ``AssetRef`` rows authorise screenplay keys as for every other edit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api.errors import ApiException
from .models import (
    ACTIVE_JOB_STATUSES,
    VERSION_MUTATING_KINDS,
    VISUAL_REVIEW_STATES,
    Asset,
    Job,
    LibraryItem,
    Project,
    ProjectVersion,
    VisualReview,
)
from .schemas.manifest import AssetManifest, MediaInfo, SceneMedia
from .schemas.screenplay import (
    MAX_VISUAL_VARIANT,
    AIVideoScene,
    BoardScene,
    InteractiveScene,
    Screenplay,
    SidePanel,
    SimulationScene,
)
from .storage.assets import canonical_json

log = logging.getLogger(__name__)

KINDS = ("image", "video", "figure", "manim", "chart", "graph", "model_3d", "terminal", "interactive", "none")
SOURCES = ("generated", "library", "upload", "figure", "manim", "builtin", "fallback", "none")
STATUSES = ("ready", "missing", "failed", "fallback", "ambiguous", "stale")
STATES = VISUAL_REVIEW_STATES
ACTIONS = ("approve", "new_version", "choose_library", "upload", "remove", "retry", "confirm_paid_retry")
VISUAL_ACTIONS = ("new_version", "choose_library", "remove", "retry")

BUILTIN_KINDS = frozenset({"chart", "graph", "model_3d", "terminal"})
MEDIA_PANEL_KINDS = frozenset({"figure", "image", "manim", "gif"})
# Side panels that are not pictures (a quiz teaser, the concept map): not reviewed, never replaced.
NON_VISUAL_PANELS = frozenset({"quiz", "skill_tree"})
# Board scenes a teacher may give a picture (a title card has its own layout).
PICTURE_SCENE_TYPES = frozenset({"content", "example", "summary", "key_takeaway", "recap"})
AMBIGUOUS_CODES = frozenset({"video.ambiguous_submission", "video.operation_lost"})
FINDING_SOURCES = frozenset({"assets", "manim"})
MAX_FINDINGS = 5
MAX_NOTE = 300
MAX_REASON = 300
FINGERPRINT_LENGTH = 32
# Build warnings that building the same version again cannot change (a per-lecture cap or a server setting; a
# version's options are frozen): no "Try again" for them. Choosing or uploading a visual is the fix.
SETTLED_WARNINGS = (
    "AI video limit reached",
    "Generated images are disabled",
    "A new AI version cannot be made on this server",
)
VARIANT_FALLBACK_REASON = "The new version could not be made, so the earlier one is shown."
CHOSEN_SHOWN_REASON = "Your choice is shown here; build the scene to use it in the lecture."
CHOOSE_HINT = " Choose a picture or video from your library, or upload one."


# ---------------------------------------------------------------------------
# The visual of a scene
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisualSlot:
    """What a scene asks its visual to show (from the screenplay only)."""

    kind: str  # KINDS
    field: str | None  # where its media lives: "side_panel" | "main" | "poster" | None
    spec: Any  # JSON request (fingerprint input; no narration, title or rationale)
    chosen_key: str | None  # the teacher's override / poster override
    figure_key: str | None  # a figure panel's source image
    variant: int
    prompt: str | None


def _panel_spec(panel: SidePanel) -> dict[str, Any]:
    return panel.model_dump(mode="json", exclude={"title", "rationale", "show_from_beat_id"})


def visual_slot(sp: Screenplay, scene: Any) -> VisualSlot:
    """The scene's visual: the main media of simulation / AI video / interactive scenes, else its side panel."""
    if isinstance(scene, SimulationScene):
        key = scene.override_asset_key
        spec = {"type": "simulation", "manim": scene.manim.model_dump(mode="json"), "override": key}
        return VisualSlot("video" if key else "manim", "main", spec, key, None, 0, None)
    if isinstance(scene, AIVideoScene):
        key = scene.override_asset_key
        spec = {"type": "ai_video", "video_prompt": scene.video_prompt,
                "fallback_image_prompt": scene.fallback_image_prompt,
                "fallback_figure_id": scene.fallback_figure_id, "override": key}
        return VisualSlot("video", "main", spec, key, None, scene.variant, scene.video_prompt)
    if isinstance(scene, InteractiveScene):
        key = scene.poster_override_asset_key
        spec = {"type": "interactive", "p5": hashlib.sha256(scene.p5_code.encode("utf-8")).hexdigest(),
                "poster": key}
        return VisualSlot("interactive", "poster", spec, key, None, 0, None)
    panel = scene.side_panel
    if panel is None or panel.kind in NON_VISUAL_PANELS:
        return VisualSlot("none", None, None, None, None, 0, None)
    key = panel.override_asset_key
    figure_key = None
    if panel.kind == "figure":
        fig = next((f for f in sp.figures if f.id == panel.figure_id), None)
        figure_key = fig.asset_key if fig is not None else None
    if panel.kind in BUILTIN_KINDS:
        kind = panel.kind
    elif panel.kind == "gif":
        kind = "image"
    elif key:  # an upload / library pick shows instead of the panel's own media
        kind = "image" if panel.kind in ("image", "figure") else "video"
    else:
        kind = panel.kind  # figure | image | manim
    prompt = panel.image_prompt if panel.kind == "image" else panel.gif_query if panel.kind == "gif" else None
    variant = panel.variant if panel.kind == "image" else 0
    return VisualSlot(kind, "side_panel", _panel_spec(panel), key, figure_key, variant, prompt)


def fingerprint(slot: VisualSlot) -> str:
    """Hash of what the scene asks its visual to show: the request, the chosen asset key, the figure's image and
    the variant. Narration, titles and rationales are not part of it; produced media is not either (a rebuild
    never re-opens a sign-off)."""
    data = {"kind": slot.kind, "spec": slot.spec, "asset": slot.chosen_key or slot.figure_key,
            "variant": slot.variant}
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def slot_media(slot: VisualSlot, media: SceneMedia | None) -> MediaInfo | None:
    """The manifest's media for the slot (None: not built, or nothing to show)."""
    if media is None or slot.field is None:
        return None
    return {"side_panel": media.side_panel, "main": media.main, "poster": media.poster}.get(slot.field)


# ---------------------------------------------------------------------------
# Status, source, actions
# ---------------------------------------------------------------------------


def _first(issues: Iterable[Mapping[str, Any]], codes: Iterable[str]) -> Mapping[str, Any] | None:
    wanted = set(codes)
    return next((i for i in issues if i.get("code") in wanted), None)


def visual_status(slot: VisualSlot, media: SceneMedia | None, issues: list[Mapping[str, Any]],
                  stale: bool) -> tuple[str, str]:
    """(status, plain-words reason) of a scene's visual."""
    found = _first(issues, AMBIGUOUS_CODES)
    if found is not None:
        return "ambiguous", ("The AI video may already have been paid for by an attempt that stopped; it is "
                             "made again only when you confirm that it may be billed again.")
    if stale:
        return "stale", "The scene changed since its media was built: build it to update the visual."
    if slot.kind == "none" or slot.kind in BUILTIN_KINDS:
        return "ready", ""
    info = slot_media(slot, media)
    if slot.chosen_key:
        if info is None:
            return "missing", "The visual you chose is no longer available: choose another one."
        if info.asset_key != slot.chosen_key:
            return "fallback", "The visual you chose is no longer available, so the scene's own visual is shown."
        return "ready", ""  # the chosen picture or clip is shown (a picture in a video scene pans slowly)
    if slot.kind == "interactive":
        return "ready", ""
    if info is None:
        failed = _first(issues, ("manim.render_failed",)) or next(
            (i for i in issues if i.get("code") == "assets.media_degraded" and " failed" in str(i.get("message"))),
            None)
        if failed is not None:
            return "failed", str(failed.get("message") or "")[:300]
        warning = next((w for w in (media.warnings if media is not None else [])), "")
        return "missing", (warning or "This visual has not been made.")[:300]
    if info.source == "fallback" or (slot.field == "main" and media is not None and media.main_is_fallback):
        warning = next((w for w in (media.warnings if media is not None else []) if "still" in w), "")
        return "fallback", (warning or "A still image is shown instead of the video.")[:300]
    return "ready", ""


def visual_source(slot: VisualSlot, media: SceneMedia | None, library_keys: Mapping[str, str]) -> str:
    """Where the visual shown comes from. ``library_keys``: the requester's library asset keys -> "library" or
    "upload" (an item uploaded for this very project)."""
    if slot.kind == "none":
        return "none"
    if slot.kind in BUILTIN_KINDS:
        return "builtin"
    info = slot_media(slot, media)
    if slot.kind == "interactive" and info is None:
        return "builtin"  # the sketch runs live; no poster frame
    if info is None:
        return "none"
    if info.source in ("upload", "poster"):
        return library_keys.get(info.asset_key or "", "upload")
    return {"image": "generated", "veo": "generated", "manim": "manim", "figure": "figure",
            "fallback": "fallback", "gif": "builtin"}.get(info.source, "none")


def can_take_picture(scene: Any) -> bool:
    """True when a library pick or upload can become this scene's visual."""
    if isinstance(scene, (SimulationScene, AIVideoScene, InteractiveScene)):
        return True
    if isinstance(scene, BoardScene) and scene.type in PICTURE_SCENE_TYPES:
        return scene.side_panel is None or scene.side_panel.kind not in NON_VISUAL_PANELS
    return False


def accepted_kinds(scene: Any) -> list[str]:
    """Library kinds a pick or upload may have for this scene (what ``check_pick`` accepts)."""
    if not can_take_picture(scene):
        return []
    if isinstance(scene, SimulationScene):
        return ["video"]
    if isinstance(scene, InteractiveScene):
        return ["image"]
    return ["image", "video"]


def settled(media: SceneMedia | None) -> bool:
    """True when the scene's media was degraded for a reason building again cannot change (``SETTLED_WARNINGS``)."""
    return media is not None and any(str(w).startswith(SETTLED_WARNINGS) for w in media.warnings)


def generation_available(options: Any, settings: Any) -> frozenset[str]:
    """Media a new version can be generated with for a lecture: ``"image"`` with the lecture's opt-in and a usable
    image provider chain, ``"video"`` likewise for AI video. ``settings``: the requester's resolved keys' settings
    (``resolve_keys(...).settings``, as their build job uses them)."""
    from .providers.factory import media_configured

    out = set()
    if getattr(options, "allow_generated_images", False) and media_configured("image", settings):
        out.add("image")
    if getattr(options, "allow_ai_video", False) and media_configured("video", settings):
        out.add("video")
    return frozenset(out)


def _video_capped(media: SceneMedia | None) -> bool:
    return media is not None and any(str(w).startswith(SETTLED_WARNINGS[0]) for w in media.warnings)


def can_new_version(scene: Any, media: SceneMedia | None = None, generate: Collection[str] | None = None) -> bool:
    """A generated visual the teacher may ask a new version of (not a chosen upload). ``generate``: the media that
    can be generated for the lecture now (``generation_available``; None: not checked). ``media``: the scene's
    built media (an AI video scene limited to a still never gets a still in place of a clip made earlier)."""
    if isinstance(scene, AIVideoScene):
        if scene.override_asset_key or scene.variant >= MAX_VISUAL_VARIANT:
            return False
        if generate is None or ("video" in generate and not _video_capped(media)):
            return True
        clip_shown = (media is not None and media.main is not None and media.main.kind == "video"
                      and not media.main_is_fallback)
        return "image" in generate and not clip_shown and not scene.fallback_figure_id
    panel = scene.side_panel
    return (panel is not None and panel.kind == "image" and bool(panel.image_prompt)
            and not panel.override_asset_key and panel.variant < MAX_VISUAL_VARIANT
            and not isinstance(scene, (SimulationScene, InteractiveScene))
            and (generate is None or "image" in generate))


def can_remove(scene: Any) -> bool:
    if isinstance(scene, (SimulationScene, AIVideoScene)):
        return bool(scene.override_asset_key)
    if isinstance(scene, InteractiveScene):
        return bool(scene.poster_override_asset_key)
    return scene.side_panel is not None and scene.side_panel.kind not in NON_VISUAL_PANELS


def can_retry(slot: VisualSlot, status: str, media: SceneMedia | None) -> bool:
    """Building the scene again can change its visual: it is not ready, and not degraded for a settled reason."""
    return (slot.kind not in ("none", *BUILTIN_KINDS) and status != "ready"
            and not (settled(media) and status in ("fallback", "missing")))


def visual_actions(scene: Any, slot: VisualSlot, status: str, *, own_lecture: bool = True,
                   media: SceneMedia | None = None, generate: Collection[str] | None = None) -> list[str]:
    """The actions that apply. Library picks and uploads (which go through the library) only in the requester's
    own lectures (``own_lecture``), like ``POST /api/library/{id}/attach``: an admin reviewing someone else's
    lecture never puts their own library media into it. A new version only when it can be generated
    (``can_new_version``); no retry for a visual a rebuild cannot change (``can_retry``). Nothing that builds the
    scene while it is skipped in the video (``hidden``: the build leaves it out)."""
    hidden = bool(getattr(scene, "hidden", False))
    acts: list[str] = []
    if slot.kind != "none":
        acts.append("approve")
    if not hidden and can_new_version(scene, media, generate):
        acts.append("new_version")
    if own_lecture and can_take_picture(scene):
        acts += ["choose_library", "upload"]
    if can_remove(scene):
        acts.append("remove")
    if not hidden and can_retry(slot, status, media):
        acts.append("retry")
    if not hidden and status == "ambiguous":
        acts.append("confirm_paid_retry")
    return acts


# ---------------------------------------------------------------------------
# Sign-off rows
# ---------------------------------------------------------------------------


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat()


def reviews_of(db: Session, version_id: int) -> dict[str, VisualReview]:
    stmt = select(VisualReview).where(VisualReview.version_id == version_id).execution_options(populate_existing=True)
    return {r.scene_id: r for r in db.execute(stmt).scalars()}


def review_view(row: VisualReview | None, current: str) -> dict[str, Any]:
    """``{"state", "stale", "note", "updated_at"}``: an approval of an earlier request reads as pending."""
    if row is None:
        return {"state": "pending", "stale": False, "note": None, "updated_at": None}
    stale = bool(row.fingerprint) and row.fingerprint != current and row.state != "pending"
    state = "pending" if row.state == "approved" and stale else row.state
    if state not in STATES:
        state = "pending"
    return {"state": state, "stale": stale, "note": row.note, "updated_at": _iso(row.updated_at)}


UNSET: Any = object()  # set_review: keep the stored note


def set_review(db: Session, version_id: int, scene_id: str, state: str, fp: str, user_id: int | None,
               note: str | None = UNSET) -> None:
    """Insert or update the scene's sign-off in one atomic statement (executed, not committed). ``note`` unset
    keeps the stored one."""
    if state not in STATES:
        raise ValueError(f"unknown review state {state!r}")
    now = dt.datetime.now(dt.timezone.utc)
    changes: dict[str, Any] = {"state": state, "fingerprint": fp, "updated_by": user_id, "updated_at": now}
    if note is not UNSET:
        changes["note"] = note
    dialect = db.get_bind().dialect.name
    if dialect in ("sqlite", "postgresql"):
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        stmt = insert(VisualReview).values(version_id=version_id, scene_id=scene_id, **changes)
        db.execute(stmt.on_conflict_do_update(index_elements=["version_id", "scene_id"], set_=changes))
        return
    row = db.execute(select(VisualReview).where(VisualReview.version_id == version_id,  # pragma: no cover
                                                VisualReview.scene_id == scene_id)).scalar_one_or_none()
    if row is None:  # pragma: no cover - other dialects
        db.add(VisualReview(version_id=version_id, scene_id=scene_id, **changes))
    else:  # pragma: no cover
        for name, value in changes.items():
            setattr(row, name, value)
    try:  # pragma: no cover
        db.flush()
    except IntegrityError:  # pragma: no cover
        db.rollback()
        raise ApiException(409, "conflict", "The review was changed at the same time; try again.") from None


# ---------------------------------------------------------------------------
# Library lookups (the requester's own items only)
# ---------------------------------------------------------------------------


def library_sources(db: Session, user_id: int, project_id: int, keys: Iterable[str]) -> dict[str, str]:
    """{asset key: "library" | "upload"} for keys in the user's library ("upload": an item uploaded for this
    project)."""
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(wanted), 500):
        stmt = select(LibraryItem.asset_key, LibraryItem.source, LibraryItem.origin_project_id).where(
            LibraryItem.user_id == user_id, LibraryItem.asset_key.in_(wanted[i:i + 500]))
        for key, source, origin in db.execute(stmt):
            out[key] = "upload" if source == "upload" and origin == project_id else "library"
    return out


def shown_keys(sp: Screenplay, manifest: AssetManifest | None) -> dict[str, str]:
    """{scene id: asset key of the visual the scene shows now (its last build)}."""
    out: dict[str, str] = {}
    if manifest is None:
        return out
    for scene in sp.scenes:
        info = slot_media(visual_slot(sp, scene), manifest.media.get(scene.id))
        if info is not None and info.asset_key:
            out[scene.id] = info.asset_key
    return out


def suggestion_counts(db: Session, user_id: int, sp: Screenplay, *, scene_ids: Collection[str] | None = None,
                      shown: Mapping[str, str] | None = None) -> dict[str, int]:
    """{scene id: how many of the user's library items fit its visual need} (``library.suggestions`` with its
    defaults: the same top 3 with a score of at least ``library.SUGGESTION_THRESHOLD`` as
    ``GET /api/library/suggestions``). ``scene_ids``: count only those scenes (same counts, less work);
    ``shown``: the media each scene shows now (``shown_keys``), never counted as a match of its own scene.
    Scenes without a need are left out. Run it last in a request: a failure is logged and gives no counts."""
    from . import library

    try:
        found = library.suggestions(db, user_id, sp, scene_ids=scene_ids, shown=shown)
    except Exception as exc:  # noqa: BLE001 - a badge only: never fails the review
        log.warning("library suggestions failed: %s", type(exc).__name__)
        return {}
    return {need.scene_id: len(matches) for need, matches in found if matches}


# ---------------------------------------------------------------------------
# Building the view
# ---------------------------------------------------------------------------


def _provenance(db: Session, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(wanted), 500):
        stmt = select(Asset.key, Asset.kind, Asset.meta).where(Asset.key.in_(wanted[i:i + 500]))
        for key, kind, meta in db.execute(stmt):
            out[key] = {"kind": kind, **(meta if isinstance(meta, dict) else {})}
    return out


def _findings(issues: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i in issues[:MAX_FINDINGS]:
        out.append({"code": str(i.get("code") or ""), "severity": str(i.get("severity") or "warning"),
                    "message": str(i.get("message") or "")[:400]})
    return out


@dataclass
class ReviewContext:
    """Everything one version's view is built from (loaded once)."""

    sp: Screenplay
    manifest: AssetManifest | None
    stale: set[str]
    issues: dict[str, list[Mapping[str, Any]]]
    rows: dict[str, VisualReview]
    library: dict[str, str]
    provenance: dict[str, dict[str, Any]]
    own_lecture: bool = True  # the requester owns the lecture (library picks allowed)
    generate: frozenset[str] | None = None  # generation_available for the requester (None: not checked)
    chosen: dict[str, MediaInfo] = field(default_factory=dict)  # stored media of the teacher's chosen keys

    def media(self, scene_id: str) -> SceneMedia | None:
        return self.manifest.media.get(scene_id) if self.manifest is not None else None


def _chosen_media(db: Session, keys: Iterable[str]) -> dict[str, MediaInfo]:
    """Stored media of the teacher's chosen asset keys (authorised in the project's screenplay already: an
    ``AssetRef`` exists for each; nothing about ownership is read here)."""
    wanted = sorted({k for k in keys if k})
    out: dict[str, MediaInfo] = {}
    for i in range(0, len(wanted), 500):
        stmt = select(Asset.key, Asset.storage_key, Asset.mime, Asset.duration_s, Asset.width, Asset.height).where(
            Asset.key.in_(wanted[i:i + 500]))
        for key, storage_key, mime, duration, width, height in db.execute(stmt):
            kind = "video" if str(mime).startswith("video/") else "image"
            out[key] = MediaInfo(asset_key=key, storage_key=storage_key, kind=kind, mime=mime, duration=duration,
                                 width=width, height=height, source="upload")
    return out


def load_context(db: Session, version: ProjectVersion, sp: Screenplay, manifest: AssetManifest | None,
                 stale: Iterable[str], user_id: int, *, generate: Collection[str] | None = None) -> ReviewContext:
    issues: dict[str, list[Mapping[str, Any]]] = {}
    for issue in version.issues or []:
        if isinstance(issue, Mapping) and issue.get("scene_id") and issue.get("source") in FINDING_SOURCES:
            issues.setdefault(str(issue["scene_id"]), []).append(issue)
    keys: list[str] = []
    chosen: list[str] = []
    for scene in sp.scenes:
        slot = visual_slot(sp, scene)
        info = slot_media(slot, manifest.media.get(scene.id) if manifest is not None else None)
        if info is not None and info.asset_key:
            keys.append(info.asset_key)
        if slot.chosen_key:
            keys.append(slot.chosen_key)
            if info is None or info.asset_key != slot.chosen_key:
                chosen.append(slot.chosen_key)  # not built yet: shown from its stored media
    owner = db.execute(select(Project.owner_id).where(Project.id == version.project_id)).scalar_one_or_none()
    return ReviewContext(sp=sp, manifest=manifest, stale=set(stale), issues=issues, rows=reviews_of(db, version.id),
                         library=library_sources(db, user_id, version.project_id, keys),
                         provenance=_provenance(db, keys), own_lecture=owner == user_id,
                         generate=frozenset(generate) if generate is not None else None,
                         chosen=_chosen_media(db, chosen))


def _shown_variant(ctx: ReviewContext, info: MediaInfo) -> int:
    try:
        return int(ctx.provenance.get(info.asset_key or "", {}).get("variant") or 0)
    except (TypeError, ValueError):
        return 0


def scene_view(ctx: ReviewContext, index: int, scene: Any, *, url_for: Any, suggestions: int = 0) -> dict[str, Any]:
    """One ``SceneVisual`` (docs: the visual-review endpoint). ``url_for(MediaInfo) -> str | None`` signs URLs.

    A visual the teacher chose (or removed) that is not built yet is shown as chosen: the chosen media with a
    note to build the scene, or no media. A new version that could not be made reads as ``fallback``."""
    slot = visual_slot(ctx.sp, scene)
    media = ctx.media(scene.id)
    issues = ctx.issues.get(scene.id, [])
    status, reason = visual_status(slot, media, issues, scene.id in ctx.stale)
    info = slot_media(slot, media)
    source = visual_source(slot, media, ctx.library)
    if status == "stale" and (info is None or info.asset_key != slot.chosen_key):
        picked = ctx.chosen.get(slot.chosen_key) if slot.chosen_key else None
        if picked is not None:
            info, source, reason = picked, ctx.library.get(picked.asset_key or "", "upload"), CHOSEN_SHOWN_REASON
        elif not slot.chosen_key and info is not None and info.source in ("upload", "poster"):
            info, source = None, "builtin" if slot.kind == "interactive" else "none"  # the choice was removed
    if (status == "ready" and slot.variant > 0 and info is not None and source in ("generated", "fallback")
            and _shown_variant(ctx, info) != slot.variant):
        status = "fallback"
        reason = next((str(w) for w in (media.warnings if media is not None else [])
                       if "new AI version" in str(w)), VARIANT_FALLBACK_REASON)[:MAX_REASON]
    if status in ("fallback", "missing") and settled(media) and ctx.own_lecture and can_take_picture(scene):
        reason = f"{reason[:MAX_REASON - len(CHOOSE_HINT)]}{CHOOSE_HINT}"
    kind = slot.kind
    if info is not None and kind in ("image", "video"):
        kind = "video" if info.kind == "video" else "image"
    meta = ctx.provenance.get(info.asset_key or "", {}) if info is not None and source in ("generated", "fallback") \
        else {}
    provider = meta.get("provider") if meta.get("kind") in ("image", "video") else None
    model = (meta.get("model") or None) if provider else None
    review = review_view(ctx.rows.get(scene.id), fingerprint(slot))
    return {
        "scene_id": scene.id,
        "index": index,
        "title": scene.title or scene.id,
        "hidden": bool(getattr(scene, "hidden", False)),
        "kind": kind,
        "source": source,
        "visual_source": source,
        "provider": provider,
        "model": model,
        "prompt": slot.prompt,
        "url": url_for(info) if info is not None and info.kind != "gif" else None,
        "poster_url": None,
        "status": status,
        "status_reason": reason,
        "review": review,
        "variant": slot.variant,
        "actions": visual_actions(scene, slot, status, own_lecture=ctx.own_lecture, media=media,
                                  generate=ctx.generate),
        "accepts": accepted_kinds(scene),
        "findings": _findings(issues),
        "library_suggestions": suggestions,
    }


def counted(item: Mapping[str, Any]) -> bool:
    """Scenes with a visual, or whose visual the teacher removed, count in the summary."""
    return item["kind"] != "none" or item["review"]["state"] == "removed"


def summary(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": 0, "approved": 0, "pending": 0, "changed": 0, "removed": 0, "needs_attention": 0}
    for item in items:
        if not counted(item):
            continue
        out["total"] += 1
        out[item["review"]["state"]] += 1
        if (item["status"] != "ready" or item["review"]["stale"]) and not item.get("hidden"):
            out["needs_attention"] += 1
    return out


# ---------------------------------------------------------------------------
# Screenplay edits of the actions
# ---------------------------------------------------------------------------


def _unavailable(message: str) -> ApiException:
    return ApiException(409, "action_unavailable", message)


def with_scene(sp: Screenplay, scene_id: str, scene: Any) -> Screenplay:
    """``sp`` with one scene replaced (validated again)."""
    data = sp.model_dump(mode="json")
    data["scenes"] = [scene.model_dump(mode="json") if s.id == scene_id else s.model_dump(mode="json")
                      for s in sp.scenes]
    return Screenplay.model_validate(data)


def new_version_scene(scene: Any, *, media: SceneMedia | None = None, generate: Collection[str] | None = None) -> Any:
    """The scene with its generated visual's ``variant`` + 1 (409 when it has none, or when it cannot be generated
    now: ``can_new_version``)."""
    if not can_new_version(scene, media, generate):
        if generate is not None and can_new_version(scene, media):
            raise _unavailable("New AI versions cannot be made for this lecture right now: generating pictures or "
                               "videos is switched off or not set up.")
        raise _unavailable("This scene has no generated picture or video to make a new version of.")
    if isinstance(scene, AIVideoScene):
        return scene.model_copy(update={"variant": scene.variant + 1})
    panel = scene.side_panel
    return scene.model_copy(update={"side_panel": panel.model_copy(update={"variant": panel.variant + 1})})


def _wrong_kind(message: str) -> ApiException:
    return ApiException(422, "validation", [{"loc": ["body", "library_item_id"], "msg": message,
                                             "type": "library_item.kind"}])


def check_pick(scene: Any, item_kind: str) -> None:
    """409 when the scene cannot show a library item, 422 when the item's kind does not fit (before anything is
    attached to the project)."""
    if not can_take_picture(scene):
        raise _unavailable("This scene cannot show a picture or a video.")
    if item_kind not in ("image", "video"):
        raise _wrong_kind("Choose a picture or a video.")
    if isinstance(scene, SimulationScene) and item_kind != "video":
        raise _wrong_kind("Choose a video for an animation scene.")
    if isinstance(scene, InteractiveScene) and item_kind != "image":
        raise _wrong_kind("Choose a picture for the still of an interactive scene.")


def chosen_scene(scene: Any, key: str, item_kind: str, *, title: str = "") -> Any:
    """The scene showing asset ``key`` (a library item of ``item_kind`` image | video) as its visual."""
    check_pick(scene, item_kind)
    if isinstance(scene, (SimulationScene, AIVideoScene)):
        return scene.model_copy(update={"override_asset_key": key})
    if isinstance(scene, InteractiveScene):
        return scene.model_copy(update={"poster_override_asset_key": key})
    panel = scene.side_panel
    wanted = ("image", "figure") if item_kind == "image" else ("manim",)
    if panel is not None and panel.kind in wanted:
        new_panel = panel.model_copy(update={"override_asset_key": key})
    else:  # a new picture panel (keeps the panel's title, rationale and timing)
        new_panel = SidePanel.model_validate({
            "kind": "image" if item_kind == "image" else "manim",
            "title": panel.title if panel is not None else (title[:160] or None),
            "rationale": panel.rationale if panel is not None else "",
            "show_from_beat_id": panel.show_from_beat_id if panel is not None else None,
            "override_asset_key": key,
        })
    return scene.model_copy(update={"side_panel": new_panel})


def removed_scene(scene: Any) -> Any:
    """The scene without its side panel, or without the teacher's override of its main media (409 otherwise)."""
    if not can_remove(scene):
        if isinstance(scene, (SimulationScene, AIVideoScene)):
            raise _unavailable("This scene's own animation or video cannot be removed; choose another visual "
                               "instead.")
        raise _unavailable("This scene has no visual to remove.")
    if isinstance(scene, (SimulationScene, AIVideoScene)):
        return scene.model_copy(update={"override_asset_key": None})
    if isinstance(scene, InteractiveScene):
        return scene.model_copy(update={"poster_override_asset_key": None})
    return scene.model_copy(update={"side_panel": None})


def removed_state(scene: Any) -> str:
    """Sign-off after ``remove``: a removed side panel is ``removed``; a removed override of main media or of a
    poster brings the scene's own visual back, which is a ``changed`` visual."""
    return "changed" if isinstance(scene, (SimulationScene, AIVideoScene, InteractiveScene)) else "removed"


def ambiguous(version: ProjectVersion, scene_id: str) -> bool:
    """True when the scene's AI video may already have been billed by a stopped attempt."""
    return any(isinstance(i, Mapping) and i.get("scene_id") == scene_id and i.get("code") in AMBIGUOUS_CODES
               for i in version.issues or [])


def write_screenplay(db: Session, version: ProjectVersion, sp: Screenplay, *, revision: int, project: Project | None,
                     store: Any, now: dt.datetime) -> int:
    """Save ``sp`` exactly like ``PUT /api/versions/{vid}/screenplay``: compare-and-set on ``revision``, refused
    while a job mutates the version, issues re-linted and merged. Executes, does not commit; returns the new
    revision (409 ``revision_conflict`` / ``version_busy``)."""
    from .api.generation import active_mutating_job
    from .api.routers.versions import safe_screenplay
    from .api.screenplay_service import (
        hidden_scene_ids,
        issues_payload,
        lint_screenplay,
        merge_issues,
        options_for,
        source_chunk_ids,
        source_ingest,
    )
    from .api.storable import ensure_storable

    document = sp.model_dump(mode="json")
    ensure_storable(document, ("body", "screenplay"))
    lint_issues = lint_screenplay(sp, options_for(project) if project is not None else None,
                                  chunk_ids=source_chunk_ids(store, version.generation_meta),
                                  ingest=source_ingest(store, version.generation_meta))
    dumped, counts = issues_payload(merge_issues(safe_screenplay(version), sp, version.issues, lint_issues),
                                    hidden_scene_ids(sp))
    job_active = exists().where(Job.version_id == version.id, Job.status.in_(ACTIVE_JOB_STATUSES),
                                Job.kind.in_(VERSION_MUTATING_KINDS))
    result = db.execute(
        update(ProjectVersion)
        .where(ProjectVersion.id == version.id, ProjectVersion.revision == revision, ~job_active)
        .values(revision=revision + 1, screenplay=document, issues=dumped, issue_counts=counts, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if (result.rowcount or 0) == 0:
        db.rollback()
        busy = active_mutating_job(db, version.id)
        if busy is not None:
            raise ApiException(409, "version_busy", "A job is currently modifying this version.", job_id=busy.id)
        current = db.execute(select(ProjectVersion.revision).where(ProjectVersion.id == version.id)).scalar_one_or_none()
        raise ApiException(409, "revision_conflict", "The screenplay was changed elsewhere.", current_revision=current)
    db.execute(update(Project).where(Project.id == version.project_id).values(updated_at=now)
               .execution_options(synchronize_session=False))
    return revision + 1


def copy_reviews(db: Session, source_version_id: int, target_version_id: int) -> int:
    """Copy a version's sign-offs to another version (``POST .../duplicate``; flushed, not committed). Returns
    the number copied; scenes the target already has a sign-off for keep theirs, so copying twice copies
    nothing. Fingerprints are kept, so a sign-off whose scene differs in the copy reads as stale."""
    if source_version_id == target_version_id:
        return 0
    existing = set(reviews_of(db, target_version_id))
    n = 0
    for row in reviews_of(db, source_version_id).values():
        if row.scene_id in existing:
            continue
        db.add(VisualReview(version_id=target_version_id, scene_id=row.scene_id, state=row.state,
                            fingerprint=row.fingerprint, note=row.note, updated_by=row.updated_by,
                            updated_at=row.updated_at))
        n += 1
    if n:
        db.flush()
    return n
