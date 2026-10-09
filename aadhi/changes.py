"""What the teacher changed since Aadhi wrote a lecture, and putting a scene back.

* **Snapshot.** At the end of a successful ``generate_lecture`` (and ``translate``, for the new version) the
  orchestrator stores the screenplay exactly as it was written as a private, content-addressed ``AssetStore``
  blob (kind ``snapshot``, JSON) and records its key in ``generation_meta["generated_snapshot_key"]``
  (``store_snapshot``). Scene regeneration and teacher saves never touch it, so it stays "what Aadhi wrote".
  It is kept out of the screenplay, so scene hashes, cache keys and the version document are unchanged. The
  cleanup GC never collects the ``snapshot`` kind. Imported lectures and versions generated before this have no
  snapshot (``available: false``).
* **Changes** (``changes``): every current scene compared with the snapshot by scene id: ``unchanged``,
  ``edited`` (the top-level scene fields whose values differ), ``added`` (not in the snapshot), ``moved`` (same
  content, out of the generated order: scenes off the longest run that kept their generated order) and
  ``removed`` (in the snapshot only; listed where it would go back: after the nearest earlier generated scene that
  kept its generated order).
  Comparisons run on both documents validated by the current schema, so fields added later with defaults never
  read as edits. ``history`` counts the earlier versions of a scene kept by scene regeneration
  (``generation_meta["scene_history"]``).
* **Revert** (``reverted``): a pure screenplay transform that puts back a scene as generated, or one of its
  history entries, in place, or restores a removed scene at a position (default: after the nearest earlier
  generated scene that kept its generated order). The caller saves it with the usual revision compare-and-set.
"""

from __future__ import annotations

import bisect
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .schemas.jsonsafe import validate_stored
from .schemas.screenplay import Scene, Screenplay
from .storage.assets import AssetStore, Produced, bytes_key

log = logging.getLogger(__name__)

SNAPSHOT_META_KEY = "generated_snapshot_key"
SNAPSHOT_KIND = "snapshot"
HISTORY_META_KEY = "scene_history"
STATUSES = ("unchanged", "edited", "added", "moved", "removed")
_CACHE_SIZE = 8
_SCENE: TypeAdapter[Any] = TypeAdapter(Scene)

_cache: OrderedDict[str, Screenplay] = OrderedDict()
_cache_lock = threading.Lock()


class RevertError(ValueError):
    """The scene cannot be put back (``code``: ``nothing_to_revert`` -> 404, ``revert_invalid`` -> 409)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def snapshot_bytes(document: Mapping[str, Any]) -> bytes:
    """The stored form of a screenplay document (compact JSON, keys in document order)."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def store_snapshot(assets: AssetStore, document: Mapping[str, Any], *, created_by: int | None = None) -> str:
    """Store ``document`` (``Screenplay.model_dump(mode="json")``) as a private snapshot blob; returns its key.
    Idempotent: identical screenplays share one blob. Blocking."""
    data = snapshot_bytes(document)
    key = bytes_key(SNAPSHOT_KIND, data)
    scenes = document.get("scenes")
    meta = {"scenes": len(scenes) if isinstance(scenes, list) else 0}
    assets.put(key, SNAPSHOT_KIND, Produced(data=data, mime="application/json", meta=meta), created_by=created_by)
    return key


def snapshot_key(meta: Mapping[str, Any] | None) -> str | None:
    key = (meta or {}).get(SNAPSHOT_META_KEY)
    return key if isinstance(key, str) and key.startswith(f"{SNAPSHOT_KIND}-") and len(key) <= 128 else None


def load_generated(store: AssetStore, meta: Mapping[str, Any] | None) -> Screenplay | None:
    """The screenplay as Aadhi wrote it (None: no snapshot, or it can no longer be read). Cached per key (the blobs
    are immutable). Blocking."""
    key = snapshot_key(meta)
    if key is None:
        return None
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    asset = store.get(key)
    if asset is None or asset.kind != SNAPSHOT_KIND:
        return None
    try:
        screenplay = validate_stored(Screenplay, json.loads(store.storage.get_bytes(asset.storage_key)))
    except (OSError, ValueError, KeyError) as exc:  # ValidationError is a ValueError
        log.warning("generated snapshot %s is unreadable: %s", key[:24], type(exc).__name__)
        return None
    with _cache_lock:
        _cache[key] = screenplay
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return screenplay


def clear_cache() -> None:
    """Tests: forget cached snapshots."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------


def history_entries(meta: Mapping[str, Any] | None, scene_id: str) -> list[dict[str, Any]]:
    """The earlier versions of a scene kept by scene regeneration (newest first)."""
    history = (meta or {}).get(HISTORY_META_KEY)
    entries = history.get(scene_id) if isinstance(history, Mapping) else None
    return [e for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else []


def history_view(meta: Mapping[str, Any] | None, scene_id: str) -> list[dict[str, Any]]:
    """History entries for the API: when, the teacher's instructions, the revision and the scene document."""
    out = []
    for e in history_entries(meta, scene_id):
        out.append({
            "at": e.get("at") if isinstance(e.get("at"), str) else None,
            "instructions": e.get("instructions") if isinstance(e.get("instructions"), str) else "",
            "revision": e.get("revision") if isinstance(e.get("revision"), int) else None,
            "scene": e.get("scene") if isinstance(e.get("scene"), Mapping) else None,
        })
    return out


def _dump(scene: Any) -> dict[str, Any]:
    return scene.model_dump(mode="json")


def fields_changed(generated: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Top-level scene fields whose values differ (current field order, then fields only the generated one has)."""
    keys = list(current) + [k for k in generated if k not in current]
    return [k for k in keys if k != "id" and generated.get(k) != current.get(k)]


def _stable(indices: Sequence[int]) -> set[int]:
    """Positions (into ``indices``) of one longest strictly increasing subsequence: the scenes that kept their
    generated order. O(n log n)."""
    tails: list[int] = []  # tails[k]: smallest last value of an increasing run of length k + 1
    tail_pos: list[int] = []
    parent = [-1] * len(indices)
    for i, value in enumerate(indices):
        k = bisect.bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_pos.append(i)
        else:
            tails[k] = value
            tail_pos[k] = i
        parent[i] = tail_pos[k - 1] if k > 0 else -1
    out: set[int] = set()
    i = tail_pos[-1] if tail_pos else -1
    while i >= 0:
        out.add(i)
        i = parent[i]
    return out


def stable_ids(generated: Screenplay, current_ids: Sequence[str]) -> set[str]:
    """The current scenes that kept their generated order (the others were moved)."""
    gen_index = {s.id: i for i, s in enumerate(generated.scenes)}
    common = [(sid, gen_index[sid]) for sid in current_ids if sid in gen_index]
    return {common[k][0] for k in _stable([g for _, g in common])}


def default_position(generated: Screenplay | None, current_ids: Sequence[str], scene_id: str,
                     stable: set[str] | None = None) -> int:
    """Where a removed scene goes back: right after the nearest earlier generated scene that is still in its
    generated order (a moved scene is no landmark), else first. Without a snapshot, or for a scene not in it: last."""
    gen_ids = [s.id for s in generated.scenes] if generated is not None else []
    if generated is None or scene_id not in gen_ids:
        return len(current_ids)
    if stable is None:
        stable = stable_ids(generated, current_ids)
    where = {sid: i for i, sid in enumerate(current_ids)}
    for sid in reversed(gen_ids[: gen_ids.index(scene_id)]):
        if sid in stable:
            return where[sid] + 1
    return 0


def changes(generated: Screenplay | None, current: Screenplay | None, meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """``{"available", "scenes": [SceneChange]}`` (see the module docstring). Without a snapshot every current
    scene is listed as ``unchanged`` with ``generated_index`` null: statuses are not known, only ``history``."""
    cur_scenes = list(current.scenes) if current is not None else []

    def entry(scene_id: str, status: str, g: int | None, c: int | None, changed: list[str]) -> dict[str, Any]:
        return {"scene_id": scene_id, "status": status, "generated_index": g, "current_index": c,
                "fields_changed": changed, "history": len(history_entries(meta, scene_id))}

    if generated is None:
        return {"available": False, "scenes": [entry(s.id, "unchanged", None, i, []) for i, s in enumerate(cur_scenes)]}
    gen_index = {s.id: i for i, s in enumerate(generated.scenes)}
    gen_dump = {s.id: _dump(s) for s in generated.scenes}
    current_ids = [s.id for s in cur_scenes]
    stable = stable_ids(generated, current_ids)
    rows: list[dict[str, Any]] = []
    for i, scene in enumerate(cur_scenes):
        g = gen_index.get(scene.id)
        if g is None:
            rows.append(entry(scene.id, "added", None, i, []))
            continue
        changed = fields_changed(gen_dump[scene.id], _dump(scene))
        status = "edited" if changed else ("unchanged" if scene.id in stable else "moved")
        rows.append(entry(scene.id, status, g, i, changed))
    present = set(current_ids)
    # removed scenes where they would go back (``default_position``), in generated order
    insert_after: dict[int, list[dict[str, Any]]] = {}
    for g, scene in enumerate(generated.scenes):
        if scene.id in present:
            continue
        anchor = default_position(generated, current_ids, scene.id, stable) - 1
        insert_after.setdefault(anchor, []).append(entry(scene.id, "removed", g, None, []))
    out = list(insert_after.get(-1, []))
    for i, row in enumerate(rows):
        out.append(row)
        out.extend(insert_after.get(i, []))
    return {"available": True, "scenes": out}


def scene_detail(generated: Screenplay | None, current: Screenplay | None, meta: Mapping[str, Any] | None,
                 scene_id: str) -> dict[str, Any] | None:
    """One scene's generated and current documents with its history (None: the scene is in neither)."""
    gen = next((s for s in generated.scenes if s.id == scene_id), None) if generated is not None else None
    cur = next((s for s in current.scenes if s.id == scene_id), None) if current is not None else None
    history = history_view(meta, scene_id)
    if gen is None and cur is None and not history:
        return None
    row = next((r for r in changes(generated, current, meta)["scenes"] if r["scene_id"] == scene_id), None)
    return {
        "available": generated is not None,
        "scene_id": scene_id,
        "status": row["status"] if row is not None else "removed",
        "fields_changed": row["fields_changed"] if row is not None else [],
        "generated": _dump(gen) if gen is not None else None,
        "current": _dump(cur) if cur is not None else None,
        "history": history,
    }


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------


def target_scene(generated: Screenplay | None, meta: Mapping[str, Any] | None, scene_id: str, to: str,
                 history_index: int | None = None) -> dict[str, Any]:
    """The scene document to put back (validated), or ``RevertError``."""
    if to == "generated":
        scene = next((s for s in generated.scenes if s.id == scene_id), None) if generated is not None else None
        if scene is None:
            raise RevertError("nothing_to_revert", "There is no version of this scene written by Aadhi to go back to.")
        return _dump(scene)
    entries = history_entries(meta, scene_id)
    index = 0 if history_index is None else history_index
    if not 0 <= index < len(entries) or not isinstance(entries[index].get("scene"), Mapping):
        raise RevertError("nothing_to_revert", "There is no earlier version of this scene to go back to.")
    try:
        scene = _SCENE.validate_python(dict(entries[index]["scene"]))
    except ValidationError:
        raise RevertError("revert_invalid", "That earlier version of the scene can no longer be used.") from None
    if scene.id != scene_id:
        raise RevertError("revert_invalid", "That earlier version belongs to another scene.")
    return _dump(scene)


def _drop_dangling(data: dict[str, Any], scene_id: str) -> None:
    """Remove the restored scene's links to lecture parts that no longer exist (concept, chapter, objectives,
    misconceptions): the scene itself is kept."""
    concepts = {c.get("id") for c in data.get("concept_map") or []}
    chapters = {c.get("id") for c in data.get("chapters") or []}
    objectives = {o.get("id") for o in data.get("learning_objectives") or []}
    miscs = {m.get("id") for m in data.get("misconceptions") or []}
    for scene in data["scenes"]:
        if scene.get("id") != scene_id:
            continue
        if scene.get("concept_id") and concepts and scene["concept_id"] not in concepts:
            scene["concept_id"] = None
        if scene.get("chapter_id") and chapters and scene["chapter_id"] not in chapters:
            scene["chapter_id"] = None
        if objectives and scene.get("objective_ids"):
            scene["objective_ids"] = [o for o in scene["objective_ids"] if o in objectives]
        if miscs and scene.get("option_misconception_ids"):
            scene["option_misconception_ids"] = [m if m in miscs else None for m in scene["option_misconception_ids"]]


def _list_in_chapter(data: dict[str, Any], ids: list[str], at: int, scene_id: str, chapter_id: Any) -> None:
    """List ``scene_id`` in chapter ``chapter_id`` after the chapter's last scene before position ``at``."""
    chapter = next((c for c in data.get("chapters") or [] if c.get("id") == chapter_id), None)
    if chapter is not None and scene_id not in chapter.get("scene_ids", []) and len(chapter["scene_ids"]) < 200:
        members = set(chapter["scene_ids"])
        before = [sid for sid in ids[:at] if sid in members]
        k = chapter["scene_ids"].index(before[-1]) + 1 if before else 0
        chapter["scene_ids"].insert(k, scene_id)


def reverted(current: Screenplay, generated: Screenplay | None, scene: Mapping[str, Any],
             position: int | None = None) -> Screenplay:
    """``current`` with ``scene`` put back: replaced in place when the scene exists (and moved back to its chapter's
    list when its chapter differs, as the editor's "Change chapter" does, so one chapter lists it), else inserted at
    ``position`` (clamped; default ``default_position``) and listed in its chapter. ``RevertError`` when the
    lecture would not validate."""
    data = current.model_dump(mode="json")
    scene_id = str(scene["id"])
    ids = [s["id"] for s in data["scenes"]]
    if scene_id in ids:
        idx = ids.index(scene_id)
        old_chapter = data["scenes"][idx].get("chapter_id")
        data["scenes"][idx] = dict(scene)
        if scene.get("chapter_id") != old_chapter:
            for ch in data.get("chapters") or []:
                if scene_id in ch.get("scene_ids", []):
                    ch["scene_ids"].remove(scene_id)
            _list_in_chapter(data, ids, idx, scene_id, scene.get("chapter_id"))
    else:
        at = default_position(generated, ids, scene_id) if position is None else max(0, min(position, len(ids)))
        data["scenes"].insert(at, dict(scene))
        _list_in_chapter(data, ids, at, scene_id, scene.get("chapter_id"))
    try:
        return Screenplay.model_validate(data)
    except ValidationError:
        _drop_dangling(data, scene_id)
    try:
        return Screenplay.model_validate(data)
    except ValidationError as exc:
        problem = exc.errors()[0].get("msg", "invalid") if exc.errors() else "invalid"
        raise RevertError("revert_invalid", f"The scene cannot be put back as it was ({str(problem)[:200]}); "
                                            "edit it by hand instead.") from None
