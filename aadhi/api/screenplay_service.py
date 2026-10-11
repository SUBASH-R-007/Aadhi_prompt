"""Screenplay-related logic used by the version endpoints.

* Asset-key authorisation: every asset key a screenplay references must be recorded for the
  project in ``asset_refs`` and be of a kind/MIME family that fits the field (``aadhi.assets_auth``:
  the per-field table and the ``AssetRef`` helper). Covers the inert Dynamic fields too: clip
  scenes, overlays, the music bed and the teacher's own narration (design §6.7).
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..assets_auth import FIELD_RULES
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


@dataclass(frozen=True)
class AssetUse:
    """One asset key referenced by a screenplay field.

    ``drop``: what an import that cannot keep the key removes instead of clearing the key, for fields whose key is
    required: the overlay (``(..., "overlays", k)``) or the clip scene (``("scenes", i)``). None: the key alone is
    cleared."""

    loc: tuple[Any, ...]
    key: str
    kinds: frozenset[str]
    mime_prefixes: tuple[str, ...]
    drop: tuple[Any, ...] | None = None

    def fits(self, asset: Asset) -> bool:
        return asset.kind in self.kinds and asset.mime.startswith(self.mime_prefixes)


def _use(loc: tuple[Any, ...], key: str, field: str, *, drop: tuple[Any, ...] | None = None) -> AssetUse:
    rule = FIELD_RULES[field]
    return AssetUse(loc, key, rule.kinds, rule.mime_prefixes, drop)


def asset_uses(sp: Screenplay) -> list[AssetUse]:
    """Every asset key in the screenplay with the kinds/MIME families allowed for its field (``FIELD_RULES``)."""
    uses: list[AssetUse] = []
    for i, fig in enumerate(sp.figures):
        if fig.asset_key:
            uses.append(_use(("figures", i, "asset_key"), fig.asset_key, "SourceFigure.asset_key"))
    for i, scene in enumerate(sp.scenes):
        base: tuple[Any, ...] = ("scenes", i)
        panel = scene.side_panel
        if panel is not None and panel.override_asset_key:
            uses.append(
                _use((*base, "side_panel", "override_asset_key"), panel.override_asset_key,
                     "SidePanel.override_asset_key")
            )
        if isinstance(scene, SimulationScene) and scene.override_asset_key:
            uses.append(
                _use((*base, "override_asset_key"), scene.override_asset_key, "SimulationScene.override_asset_key")
            )
        if isinstance(scene, AIVideoScene) and scene.override_asset_key:
            uses.append(
                _use((*base, "override_asset_key"), scene.override_asset_key, "AIVideoScene.override_asset_key")
            )
        if isinstance(scene, InteractiveScene) and scene.poster_override_asset_key:
            uses.append(
                _use((*base, "poster_override_asset_key"), scene.poster_override_asset_key,
                     "InteractiveScene.poster_override_asset_key")
            )
        uses.extend(_dynamic_scene_uses(scene, base))
    music = getattr(getattr(sp, "presentation", None), "music", None)
    music_key = getattr(music, "asset_key", None)
    if music_key:
        uses.append(_use(("presentation", "music", "asset_key"), music_key, "Presentation.music.asset_key"))
    return uses


def _dynamic_scene_uses(scene: Any, base: tuple[Any, ...]) -> list[AssetUse]:
    """Keys of the Dynamic fields of a scene (design §6.7, §8.2): the clip of a clip scene, beat overlays and the
    teacher's own narration of a beat (in ``beats`` and a quiz's ``reveal_beats``).

    Read by attribute, not by scene class, so every scene class that carries one of these fields is covered."""
    uses: list[AssetUse] = []
    clip_key = getattr(getattr(scene, "clip", None), "asset_key", None)
    if clip_key:
        uses.append(_use((*base, "clip", "asset_key"), clip_key, "ClipScene.clip.asset_key", drop=base))
    for name in ("beats", "reveal_beats"):
        for j, beat in enumerate(getattr(scene, name, None) or ()):
            loc = (*base, name, j)
            for k, overlay in enumerate(getattr(beat, "overlays", None) or ()):
                key = getattr(getattr(overlay, "clip", None), "asset_key", None)
                if key:
                    uses.append(_use((*loc, "overlays", k, "clip", "asset_key"), key, "Overlay.clip.asset_key",
                                     drop=(*loc, "overlays", k)))
            voice_key = getattr(beat, "voice_override_asset_key", None)
            if voice_key:
                uses.append(_use((*loc, "voice_override_asset_key"), voice_key, "Beat.voice_override_asset_key"))
    return uses


_IN_BATCH = 500  # bound values per IN list: well under SQLite's limit (32766), like record_asset_refs / review.py


def _referenced_assets(db: Session, project_ids: Iterable[int], keys: Iterable[str]) -> dict[str, Asset]:
    """Asset rows of ``keys`` that one of ``project_ids`` references (asset_refs).

    A Dynamic screenplay can name tens of thousands of keys (overlays and voice overrides per beat) and an admin's
    import checks every project, so both lists go to the database in batches of ``_IN_BATCH``."""
    keys = sorted(set(keys))
    project_ids = sorted(set(project_ids))
    if not keys or not project_ids:
        return {}
    found: dict[str, Asset] = {}
    for i in range(0, len(keys), _IN_BATCH):
        wanted = keys[i:i + _IN_BATCH]
        for j in range(0, len(project_ids), _IN_BATCH):
            if not wanted:  # every key of this batch found through an earlier group of projects
                break
            stmt = (
                select(Asset)
                .join(AssetRef, AssetRef.asset_key == Asset.key)
                .where(AssetRef.project_id.in_(project_ids[j:j + _IN_BATCH]), Asset.key.in_(wanted))
            )
            found.update((a.key, a) for a in db.execute(stmt).scalars())
            wanted = [k for k in wanted if k not in found]
    return found


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
        elif not use.fits(asset):
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
    """Imports: drop asset keys the user cannot use. Returns (screenplay, warnings, kept keys).

    An optional key is cleared; an overlay or a clip scene whose clip cannot be kept is removed (its key is
    required), and a removed scene leaves the chapters that listed it (``strip_asset_uses``)."""
    uses = asset_uses(sp)
    if not uses:
        return sp, [], set()
    found = _referenced_assets(db, project_ids, (u.key for u in uses))
    kept: set[str] = set()
    refused: list[AssetUse] = []
    for use in uses:
        asset = found.get(use.key)
        if asset is not None and use.fits(asset):
            kept.add(use.key)
        else:
            refused.append(use)
    if not refused:
        return sp, [], kept
    data = sp.model_dump(mode="json")
    warnings = strip_asset_uses(data, refused)
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


_DROPPED = {"scenes": "the clip scene", "overlays": "the overlay"}


def strip_asset_uses(data: dict[str, Any], uses: Iterable[AssetUse]) -> list[str]:
    """Remove ``uses`` from a dumped screenplay ``data`` in place; one warning per use.

    Keys are cleared first; then the overlays and clip scenes to drop are deleted, deepest and last first, so the
    stored indices stay valid. A removed scene's id leaves ``chapters[].scene_ids`` and any ``look.continues_from``
    that named it."""
    warnings: list[str] = []
    drops: set[tuple[Any, ...]] = set()
    for use in uses:
        where = ".".join(str(p) for p in use.loc)
        if use.drop is None:
            node: Any = data
            for part in use.loc[:-1]:
                node = node[part]
            node[use.loc[-1]] = None
            warnings.append(f"Removed unavailable media reference at {where}")
        else:
            drops.add(use.drop)
            what = _DROPPED.get(str(use.drop[-2]), "the item")
            warnings.append(f"Removed unavailable media reference at {where} ({what} was removed)")
    removed_scenes: set[str] = set()
    # Reverse tuple order: an overlay before its scene, a later index before an earlier one. Paths are compared
    # position by position and every position holds the same type in all of them (str names, int indices).
    for path in sorted(drops, reverse=True):
        parent: Any = data
        for part in path[:-1]:
            parent = parent[part]
        if path[:-1] == ("scenes",):
            removed_scenes.add(str(parent[path[-1]].get("id")))
        del parent[path[-1]]
    if removed_scenes:
        for chapter in data.get("chapters") or []:
            chapter["scene_ids"] = [s for s in chapter.get("scene_ids") or [] if s not in removed_scenes]
        for scene in data.get("scenes") or []:
            look = scene.get("look")
            if isinstance(look, dict) and look.get("continues_from") in removed_scenes:
                look["continues_from"] = None
    return warnings


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


# ---------------------------------------------------------------------------
# Dynamic-video write gates (DYNAMIC_FEATURES; design §6.7, aadhi.schemas.features)
# ---------------------------------------------------------------------------


def unavailable_features(
    sp: Screenplay, enabled: Iterable[str], *, already: Callable[[], Screenplay | None] | None = None
) -> dict[str, list[tuple[Any, ...]]]:
    """Features ``sp`` uses that this server does not enable (feature -> locations, walk order).

    ``already``: returns the stored screenplay the write replaces (called only when needed); a feature it already
    uses stays writable, so an edit never fails on what the lecture already had (e.g. after a feature was switched
    off again). Imports have no stored document.
    """
    from ..schemas.features import disabled_features, features_used

    used = disabled_features(sp, enabled)
    if used and already is not None:
        stored = already()
        if stored is not None:
            for name in set(features_used(stored)):
                used.pop(name, None)
    return used


def require_features(
    sp: Screenplay,
    settings: Any,
    *,
    prefix: tuple[Any, ...] = ("body", "screenplay"),
    already: Callable[[], Screenplay | None] | None = None,
) -> None:
    """422 ``feature_unavailable`` when ``sp`` uses a v3 feature that ``DYNAMIC_FEATURES`` does not enable.

    ``detail`` lists one entry per feature (at its first location, pydantic-style); ``features`` names them."""
    from ..schemas.features import FEATURE_LABELS

    missing = unavailable_features(sp, getattr(settings, "dynamic_features", None) or (), already=already)
    if not missing:
        return
    detail = [
        {
            "loc": [*prefix, *locs[0]],
            "msg": f"{FEATURE_LABELS.get(name, name)}: not available on this server yet "
                   f"(DYNAMIC_FEATURES does not include {name!r}).",
            "type": "feature_unavailable",
        }
        for name, locs in missing.items()  # in the order the features are first met
    ]
    raise ApiException(422, "feature_unavailable", detail, features=sorted(missing))
