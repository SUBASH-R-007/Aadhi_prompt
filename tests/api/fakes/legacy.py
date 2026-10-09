"""Fake aadhi.legacy: v1 = {"title", "scenes": [{"title", "narration"}]} without schema_version 2."""

from __future__ import annotations

from aadhi.schemas.screenplay import Screenplay


def is_legacy(data) -> bool:
    return (
        isinstance(data, dict)
        and data.get("schema_version") != 2
        and isinstance(data.get("scenes"), list)
        and all(isinstance(s, dict) and "narration" in s and "beats" not in s for s in data["scenes"])
    )


def convert_legacy(data):
    scenes = []
    for i, s in enumerate(data["scenes"]):
        scenes.append(
            {
                "id": f"s{i + 1}",
                "type": "content",
                "title": s.get("title", ""),
                "beats": [{"id": f"s{i + 1}-b1", "narration": s["narration"]}],
                "board": [],
            }
        )
    sp = Screenplay.model_validate({"session_title": data.get("title", ""), "scenes": scenes})
    return sp, ["Converted from a v1 lecture"]


def import_legacy_db(db, path, settings, *, owner_fallback: str = "admin") -> dict:
    return {"project_ids": []}
