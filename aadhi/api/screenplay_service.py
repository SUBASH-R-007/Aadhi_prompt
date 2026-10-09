"""Screenplay-related logic used by the version endpoints.

* Asset-key authorisation: every asset key a screenplay references must be recorded for the
  project in ``asset_refs`` and be of a kind/MIME family that fits the field.
* Stale scenes: scenes whose content hash differs from the hash the manifest was built from.
* Issue bookkeeping after edits (fresh lint + still-valid critic/manim/asset issues). Lint gets
  the source's chunk ids (exact ``source.unknown_ref`` checks, as at generation time) from the
  version's content-addressed ingest extract, cached per extract key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, AssetRef, Project
from ..pipeline.base import GenerationOptions, Issue
from ..schemas.manifest import AssetManifest
from ..schemas.screenplay import (
    AIVideoScene,
    InteractiveScene,
    Screenplay,
    SimulationScene,
)
from ..storage.assets import AssetStore
from .errors import ApiException
from .util import canonical_json

logger = logging.getLogger(__name__)

VISUAL = ("image/",)
MOTION = ("video/",)
VISUAL_OR_MOTION = ("image/", "video/")


@dataclass(frozen=True)
class AssetUse:
    """One asset key referenced by a screenplay field."""

    loc: tuple[Any, ...]
    key: str
    kinds: frozenset[str]
    mime_prefixes: tuple[str, ...]


def asset_uses(sp: Screenplay) -> list[AssetUse]:
    """Every asset key in the screenplay with the kinds/MIME families allowed for its field."""
    uses: list[AssetUse] = []
    for i, fig in enumerate(sp.figures):
        if fig.asset_key:
            uses.append(AssetUse(("figures", i, "asset_key"), fig.asset_key, frozenset({"figure", "upload"}), VISUAL))
    for i, scene in enumerate(sp.scenes):
        base: tuple[Any, ...] = ("scenes", i)
        panel = scene.side_panel
        if panel is not None and panel.override_asset_key:
            uses.append(
                AssetUse(
                    (*base, "side_panel", "override_asset_key"),
                    panel.override_asset_key,
                    frozenset({"upload", "image", "video", "figure", "manim"}),
                    VISUAL_OR_MOTION,
                )
            )
        if isinstance(scene, SimulationScene) and scene.override_asset_key:
            uses.append(
                AssetUse(
                    (*base, "override_asset_key"),
                    scene.override_asset_key,
                    frozenset({"upload", "video", "manim"}),
                    MOTION,
                )
            )
        if isinstance(scene, AIVideoScene) and scene.override_asset_key:
            uses.append(
                AssetUse(
                    (*base, "override_asset_key"),
                    scene.override_asset_key,
                    frozenset({"upload", "image", "video", "figure"}),
                    VISUAL_OR_MOTION,
                )
            )
        if isinstance(scene, InteractiveScene) and scene.poster_override_asset_key:
            uses.append(
                AssetUse(
                    (*base, "poster_override_asset_key"),
                    scene.poster_override_asset_key,
                    frozenset({"upload", "image", "poster", "figure"}),
                    VISUAL,
                )
            )
    return uses


def _referenced_assets(db: Session, project_ids: Iterable[int], keys: Iterable[str]) -> dict[str, Asset]:
    keys = sorted(set(keys))
    project_ids = list(set(project_ids))
    if not keys or not project_ids:
        return {}
    stmt = (
        select(Asset)
        .join(AssetRef, AssetRef.asset_key == Asset.key)
        .where(AssetRef.project_id.in_(project_ids), Asset.key.in_(keys))
    )
    return {a.key: a for a in db.execute(stmt).scalars()}


def asset_key_errors(
    db: Session, project_ids: Iterable[int], sp: Screenplay, *, prefix: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    """Pydantic-style error entries for unauthorised / wrongly typed asset keys ([] = all fine)."""
    uses = asset_uses(sp)
    if not uses:
        return []
    found = _referenced_assets(db, project_ids, (u.key for u in uses))
    errors: list[dict[str, Any]] = []
    for use in uses:
        asset = found.get(use.key)
        loc = ["body", *prefix, *use.loc]
        if asset is None:
            errors.append(
                {"loc": loc, "msg": "asset key is not available to this project", "type": "asset_key.unknown"}
            )
        elif asset.kind not in use.kinds or not asset.mime.startswith(use.mime_prefixes):
            errors.append(
                {
                    "loc": loc,
                    "msg": f"asset of kind {asset.kind!r} ({asset.mime}) cannot be used here",
                    "type": "asset_key.kind",
                }
            )
    return errors


def authorize_asset_keys(
    db: Session, project_id: int, sp: Screenplay, *, prefix: tuple[Any, ...] = ("screenplay",)
) -> None:
    """422 ``validation`` unless every asset key is referenced by the project with a fitting kind."""
    errors = asset_key_errors(db, [project_id], sp, prefix=prefix)
    if errors:
        raise ApiException(422, "validation", errors)


def accessible_project_ids(db: Session, user_id: int, *, admin: bool) -> list[int]:
    """Projects whose asset refs a user may re-use (imports of their own exports)."""
    stmt = select(Project.id).where(Project.deleted_at.is_(None))
    if not admin:
        stmt = stmt.where(Project.owner_id == user_id)
    return list(db.execute(stmt).scalars())


def strip_unauthorized_asset_keys(
    db: Session, sp: Screenplay, *, project_ids: list[int]
) -> tuple[Screenplay, list[str], set[str]]:
    """Imports: drop asset keys the user cannot use. Returns (screenplay, warnings, kept keys)."""
    uses = asset_uses(sp)
    if not uses:
        return sp, [], set()
    found = _referenced_assets(db, project_ids, (u.key for u in uses))
    data = sp.model_dump(mode="json")
    warnings: list[str] = []
    kept: set[str] = set()
    for use in uses:
        asset = found.get(use.key)
        if asset is not None and asset.kind in use.kinds and asset.mime.startswith(use.mime_prefixes):
            kept.add(use.key)
            continue
        node: Any = data
        for part in use.loc[:-1]:
            node = node[part]
        node[use.loc[-1]] = None
        warnings.append(f"Removed unavailable media reference at {'.'.join(str(p) for p in use.loc)}")
    if not warnings:
        return sp, [], kept
    try:
        return Screenplay.model_validate(data), warnings, kept
    except ValidationError as exc:
        raise ApiException(
            422,
            "validation",
            [
                {"loc": ["file", *e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
                for e in exc.errors()
            ]
            + [{"loc": ["file"], "msg": w, "type": "asset_key.unknown"} for w in warnings],
        ) from None


def scene_hash(scene: Any) -> str:
    """Content hash used for staleness (pipeline's implementation; sha256 fallback)."""
    try:
        from ..pipeline.assets import scene_hash as pipeline_scene_hash
    except ImportError:  # pragma: no cover - pipeline area not installed
        return hashlib.sha256(canonical_json(scene.model_dump(mode="json")).encode("utf-8")).hexdigest()
    return str(pipeline_scene_hash(scene))


def stale_scenes(sp: Screenplay | None, manifest: AssetManifest | None) -> list[str]:
    """Scenes whose assets are missing, flagged, or built from different content (screenplay order).

    Uses the pipeline's exact rule (``stale_scene_ids``: hash incl. the lexicon entries that affect
    speech) when available, else compares ``scene_hash`` with the manifest's hashes.
    """
    if sp is None:
        return []
    if manifest is None:
        return [s.id for s in sp.scenes]
    try:
        from ..pipeline.assets import stale_scene_ids
    except ImportError:
        stale = {s.id for s in sp.scenes if manifest.scene_hashes.get(s.id) != scene_hash(s)}
    else:
        stale = set(stale_scene_ids(sp, manifest))
    stale |= set(manifest.stale_scenes)
    return [s.id for s in sp.scenes if s.id in stale]


def lint_screenplay(
    sp: Screenplay,
    options: GenerationOptions | None,
    *,
    chunk_ids: set[str] | None = None,
    ingest: Any | None = None,
) -> list[Issue]:
    """Deterministic lint (``aadhi.pipeline.validate.lint``).

    ``chunk_ids`` enables exact source-ref checks; ``ingest`` (the version's IngestResult) also
    enables the check that administrative values removed from the source (an SME's name, course
    codes ...) never appear in the lecture after a teacher's edit.
    """
    from ..pipeline.validate import lint

    return list(lint(sp, options, chunk_ids=chunk_ids, ingest=ingest))


INGEST_KEY = "ingest_key"  # generation_meta entry: asset key of the IngestResult extract (kind "extract")
_CHUNK_CACHE_SIZE = 64
_chunk_cache: OrderedDict[str, frozenset[str]] = OrderedDict()
_chunk_lock = threading.Lock()


def _read_chunk_ids(store: AssetStore, key: str) -> frozenset[str] | None:
    asset = store.get(key)
    if asset is None:
        return None
    try:
        data = json.loads(store.storage.get_bytes(asset.storage_key))
    except (OSError, ValueError) as exc:
        logger.warning("ingest extract %s is unreadable: %s", key[:24], type(exc).__name__)
        return None
    chunks = data.get("chunks") if isinstance(data, Mapping) else None
    if not isinstance(chunks, list):
        return None
    return frozenset(c["id"] for c in chunks if isinstance(c, Mapping) and isinstance(c.get("id"), str))


def source_chunk_ids(store: AssetStore, generation_meta: Mapping[str, Any] | None) -> set[str] | None:
    """Chunk ids of the source the version was generated from (None: unknown, e.g. imports).

    Extracts are content-addressed (immutable), so the id set is cached per extract key. Blocking:
    call from sync endpoints only.
    """
    key = (generation_meta or {}).get(INGEST_KEY)
    if not isinstance(key, str) or not key:
        return None
    with _chunk_lock:
        cached = _chunk_cache.get(key)
        if cached is not None:
            _chunk_cache.move_to_end(key)
            return set(cached) or None
    ids = _read_chunk_ids(store, key)
    if ids is None:
        return None
    with _chunk_lock:
        _chunk_cache[key] = ids
        _chunk_cache.move_to_end(key)
        while len(_chunk_cache) > _CHUNK_CACHE_SIZE:
            _chunk_cache.popitem(last=False)
    return set(ids) or None


_INGEST_CACHE_SIZE = 16
_ingest_cache: OrderedDict[str, Any] = OrderedDict()


def source_ingest(store: AssetStore, generation_meta: Mapping[str, Any] | None) -> Any | None:
    """The IngestResult the version was generated from (None for imports / unreadable extracts).

    Cached per content-addressed extract key (immutable). Blocking: call from sync endpoints only.
    """
    key = (generation_meta or {}).get(INGEST_KEY)
    if not isinstance(key, str) or not key:
        return None
    with _chunk_lock:
        cached = _ingest_cache.get(key)
        if cached is not None:
            _ingest_cache.move_to_end(key)
            return cached
    asset = store.get(key)
    if asset is None:
        return None
    try:
        from ..pipeline.base import IngestResult

        ingest = IngestResult.model_validate_json(store.storage.get_bytes(asset.storage_key))
    except (OSError, ValueError) as exc:
        logger.warning("ingest extract %s is unreadable: %s", key[:24], type(exc).__name__)
        return None
    with _chunk_lock:
        _ingest_cache[key] = ingest
        _ingest_cache.move_to_end(key)
        while len(_ingest_cache) > _INGEST_CACHE_SIZE:
            _ingest_cache.popitem(last=False)
    return ingest


def clear_chunk_cache() -> None:
    """Tests: forget cached chunk-id sets and extracts."""
    with _chunk_lock:
        _chunk_cache.clear()
        _ingest_cache.clear()


def options_for(project: Project) -> GenerationOptions | None:
    """The project's stored GenerationOptions (None if missing/invalid)."""
    try:
        return GenerationOptions.model_validate(dict(project.settings or {}))
    except ValidationError:
        return None


def dump_issue(issue: Any) -> dict[str, Any]:
    """Issue -> JSON dict."""
    return issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue)


def issues_payload(
    issues: Iterable[Any], hidden: Iterable[str] = frozenset()
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """(dumped issues, counts by severity) — same bookkeeping as ``ProjectVersion.set_issues``.

    ``hidden``: ids of the hidden scenes. Their issues are stored at their real severity but counted as notes
    (``pipeline.validate.hidden_as_notes``), so the project list does not count them; empty: counted as stored."""
    dumped = [dump_issue(i) for i in issues]
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in served_issues(dumped, hidden):
        sev = str(item.get("severity", "warning"))
        counts[sev] = counts.get(sev, 0) + 1
    return dumped, counts


def served_issues(issues: Iterable[Any], hidden: Iterable[str]) -> list[Any]:
    """Stored issues as served to the editor: those of the ``hidden`` scenes as notes (``pipeline.validate``)."""
    from ..pipeline.validate import hidden_as_notes

    return hidden_as_notes(issues, hidden)


def hidden_scene_ids(screenplay: Any) -> frozenset[str]:
    """Ids of the hidden scenes of a Screenplay or a stored screenplay document (``pipeline.validate.hidden_ids``)."""
    from ..pipeline.validate import hidden_ids

    return hidden_ids(screenplay)


def merge_issues(
    old_sp: Screenplay | None, new_sp: Screenplay, old_issues: list[dict[str, Any]] | None, lint_issues: Iterable[Any]
) -> list[dict[str, Any]]:
    """Fresh lint issues + non-lint issues of scenes whose content did not change."""
    merged = [dump_issue(i) for i in lint_issues]
    old_hashes = {s.id: scene_hash(s) for s in old_sp.scenes} if old_sp is not None else {}
    new_hashes = {s.id: scene_hash(s) for s in new_sp.scenes}
    for issue in old_issues or []:
        if not isinstance(issue, dict) or issue.get("source", "lint") == "lint":
            continue
        sid = issue.get("scene_id")
        if sid is None or (sid in new_hashes and old_hashes.get(sid) == new_hashes[sid]):
            merged.append(issue)
    return merged
