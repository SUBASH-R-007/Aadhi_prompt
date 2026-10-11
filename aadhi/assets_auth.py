"""Asset authorisation: which stored media a project may name, and how an asset becomes usable in a project.

A screenplay names stored media by asset key (an upload, an extracted figure, a generated clip, a music bed ...).
Keys are content-addressed, so they can be guessed or copied out of another teacher's export. A key is therefore
usable in a project only when

1. the project has an ``AssetRef`` row for it (``record_asset_refs``), and
2. the asset's kind and MIME family fit the field that names it (``FIELD_RULES``).

``aadhi.api.screenplay_service.asset_uses`` lists every key a screenplay names with its rule; ``PUT /screenplay``,
the timeline preview, change requests and Visual Review refuse unfit keys with 422, and import strips them.

* ``FIELD_RULES``: the per-field table of design §6.7 "Asset authorisation" (frozen in B0). It already covers the
  inert Dynamic fields (clip scenes, overlays, the music bed, the teacher's own narration), so those keys are
  authorised from the day the fields exist, before their features ship.
* ``record_asset_refs``: the one helper that makes keys usable in a project. Call it whenever a project uploads,
  normalises, generates or adopts an asset (uploads, ``normalize_clip``, TTS / images / clips / music / renders,
  library items, imports). Idempotent and safe under concurrency.

Dependency-light (models + SQLAlchemy) so the API, the pipeline and the workers can all import it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import AssetRef

Family = Literal["visual", "motion", "visual_or_motion", "audio"]

# MIME prefixes of each family: the asset's ``mime`` must start with one of them.
FAMILY_MIMES: dict[str, tuple[str, ...]] = {
    "visual": ("image/",),
    "motion": ("video/",),
    "visual_or_motion": ("image/", "video/"),
    "audio": ("audio/",),
}


@dataclass(frozen=True)
class FieldRule:
    """Asset kinds and MIME family allowed for one key-bearing screenplay field."""

    kinds: frozenset[str]
    family: Family

    @property
    def mime_prefixes(self) -> tuple[str, ...]:
        return FAMILY_MIMES[self.family]

    def fits(self, kind: str, mime: str) -> bool:
        return kind in self.kinds and str(mime).startswith(self.mime_prefixes)


_CLIP = frozenset({"clip", "upload", "video"})  # normalised mezzanine, a teacher's upload, a generated clip

# Field (``Model.path``) -> rule. The first five are the classic fields, unchanged; the last four are the Dynamic
# fields of §6.7 / §8.2 (inert until their features ship; their writes are gated by DYNAMIC_FEATURES).
FIELD_RULES: dict[str, FieldRule] = {
    "SourceFigure.asset_key": FieldRule(frozenset({"figure", "upload"}), "visual"),
    "SidePanel.override_asset_key": FieldRule(frozenset({"upload", "image", "video", "figure", "manim"}),
                                              "visual_or_motion"),
    "SimulationScene.override_asset_key": FieldRule(frozenset({"upload", "video", "manim"}), "motion"),
    "AIVideoScene.override_asset_key": FieldRule(frozenset({"upload", "image", "video", "figure"}), "visual_or_motion"),
    "InteractiveScene.poster_override_asset_key": FieldRule(frozenset({"upload", "image", "poster", "figure"}),
                                                            "visual"),
    "ClipScene.clip.asset_key": FieldRule(_CLIP, "motion"),
    "Overlay.clip.asset_key": FieldRule(_CLIP, "motion"),
    "Presentation.music.asset_key": FieldRule(frozenset({"music", "upload"}), "audio"),
    "Beat.voice_override_asset_key": FieldRule(frozenset({"narration_upload"}), "audio"),
}


def record_asset_refs(db: Session, project_id: int, keys: Iterable[str]) -> int:
    """Make ``keys`` usable in ``project_id``: insert the missing ``AssetRef`` rows. Returns how many were added.

    Idempotent (empty keys and duplicates are ignored) and safe under concurrency: one ``INSERT … ON CONFLICT DO
    NOTHING`` per 500 keys on SQLite and PostgreSQL, a savepoint per key elsewhere. Never commits: the caller's
    transaction decides, so a failed request leaves no grant behind.
    """
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return 0
    dialect = db.get_bind().dialect.name
    rows = [{"project_id": project_id, "asset_key": k} for k in wanted]
    if dialect in ("sqlite", "postgresql"):
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        added = 0
        for i in range(0, len(rows), 500):
            stmt = insert(AssetRef).values(rows[i:i + 500]).on_conflict_do_nothing(
                index_elements=["project_id", "asset_key"]
            )
            added += db.execute(stmt).rowcount or 0
        return added
    existing = set(db.execute(
        select(AssetRef.asset_key).where(AssetRef.project_id == project_id, AssetRef.asset_key.in_(wanted))
    ).scalars())
    added = 0
    for k in wanted:
        if k in existing:
            continue
        try:
            with db.begin_nested():
                db.add(AssetRef(project_id=project_id, asset_key=k))
            added += 1
        except IntegrityError:
            pass
    return added
