"""Asset authorisation (design §6.7, B0): the per-field table, the ``AssetRef`` helper, the key fields that
``asset_uses`` finds (classic and Dynamic), the 422 errors and what an import strips.

Most tests feed ``asset_uses`` plain attribute documents (``SimpleNamespace``), as it reads the Dynamic fields by
name; two pin it to the real schema (every ``*asset_key*`` field is in the table; the maximal Dynamic document of
the transform-survival test). ``tests/api/test_asset_auth_dynamic.py`` covers the rules end to end through
``PUT /screenplay``, the preview, import and uploads.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from aadhi.api.errors import ApiException
from aadhi.api.screenplay_service import (
    AssetUse,
    asset_key_errors,
    asset_uses,
    authorize_asset_keys,
    strip_asset_uses,
)
from aadhi.assets_auth import FAMILY_MIMES, FIELD_RULES, record_asset_refs
from aadhi.models import Asset, AssetRef, Project, User
from aadhi.pipeline import dbops
from tests.api.factories import screenplay_dict

# --- helpers -----------------------------------------------------------------------------------------


def add_project(db, name: str) -> Project:
    user = User(username=name, password_hash="x", role="editor")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title=f"{name}'s lesson")
    db.add(project)
    db.commit()
    return project


def add_asset(db, key: str, kind: str, mime: str) -> Asset:
    asset = Asset(key=key, kind=kind, storage_key=f"assets/{kind}/{key}/x.bin", mime=mime, size_bytes=1)
    db.add(asset)
    db.commit()
    return asset


def refs(db, project: Project) -> list[str]:
    return sorted(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project.id)).scalars())


def ns(value: Any) -> Any:
    """A dumped document as attribute objects (what ``asset_uses`` reads), without the v3 schema classes."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [ns(v) for v in value]
    return value


def dynamic_doc() -> dict[str, Any]:
    """A dumped Dynamic screenplay with every new key field: overlays (in beats and a quiz's reveal beats), the
    teacher's narration, a clip scene with a voice-over overlay, a split scene continuing from the clip, music."""
    return {
        "figures": [],
        "chapters": [{"id": "ch1", "title": "Part 1", "scene_ids": ["s1", "c1", "s2", "q1"]}],
        "scenes": [
            {
                "id": "s1",
                "type": "content",
                "side_panel": None,
                "beats": [
                    {
                        "id": "b1",
                        "narration": "One.",
                        "overlays": [
                            {"id": "o1", "clip": {"asset_key": "clip-bob", "in_frame": 0, "out_frame": 60}},
                            {"id": "o2", "clip": {"asset_key": "clip-own", "in_frame": 0, "out_frame": 60}},
                        ],
                        "voice_override_asset_key": "voice-bob",
                    },
                    {"id": "b2", "narration": "Two.", "voice_override_asset_key": "voice-own"},
                ],
            },
            {
                "id": "c1",
                "type": "clip",
                "side_panel": None,
                "clip": {"asset_key": "clip-bob-2", "in_frame": 0, "out_frame": 90},
                "beats": [
                    {
                        "id": "cb1",
                        "narration": "Watch.",
                        "overlays": [{"id": "o3", "clip": {"asset_key": "clip-bob", "in_frame": 0, "out_frame": 30}}],
                    }
                ],
            },
            {"id": "s2", "type": "content", "side_panel": None, "look": {"continues_from": "c1"}, "beats": []},
            {
                "id": "q1",
                "type": "quiz_checkpoint",
                "side_panel": None,
                "beats": [{"id": "qb1", "narration": "Which?"}],
                "reveal_beats": [
                    {
                        "id": "qr1",
                        "narration": "This.",
                        "overlays": [{"id": "o4", "clip": {"asset_key": "clip-own", "in_frame": 0, "out_frame": 30}}],
                    }
                ],
            },
        ],
        "presentation": {"music": {"source": "library", "asset_key": "music-bob"}},
    }


# --- the frozen per-field table ----------------------------------------------------------------------------

FROZEN_TABLE = {
    # classic fields: unchanged by B0
    "SourceFigure.asset_key": ({"figure", "upload"}, "visual"),
    "SidePanel.override_asset_key": ({"upload", "image", "video", "figure", "manim"}, "visual_or_motion"),
    "SimulationScene.override_asset_key": ({"upload", "video", "manim"}, "motion"),
    "AIVideoScene.override_asset_key": ({"upload", "image", "video", "figure"}, "visual_or_motion"),
    "InteractiveScene.poster_override_asset_key": ({"upload", "image", "poster", "figure"}, "visual"),
    # design §6.7 "Asset authorisation"
    "ClipScene.clip.asset_key": ({"clip", "upload", "video"}, "motion"),
    "Overlay.clip.asset_key": ({"clip", "upload", "video"}, "motion"),
    "Presentation.music.asset_key": ({"music", "upload"}, "audio"),
    "Beat.voice_override_asset_key": ({"narration_upload"}, "audio"),
}


def test_field_rules_are_the_frozen_table() -> None:
    assert {name: (set(rule.kinds), rule.family) for name, rule in FIELD_RULES.items()} == FROZEN_TABLE
    assert FAMILY_MIMES == {
        "visual": ("image/",),
        "motion": ("video/",),
        "visual_or_motion": ("image/", "video/"),
        "audio": ("audio/",),
    }


def test_dynamic_asset_kinds_are_public_and_calibration_stays_private() -> None:
    """Design §6.7 "Asset kinds": the new kinds are served from /media like the classic media; calibration is not."""
    from aadhi.models import PRIVATE_ASSET_KINDS, PUBLIC_ASSET_KINDS
    from aadhi.storage.assets import storage_key_for

    dynamic = {"music", "clip", "clip_proxy", "clip_sprite", "waveform", "voice_sample", "thumbnail", "narration_upload"}
    assert dynamic <= set(PUBLIC_ASSET_KINDS)
    assert {"upload", "figure", "tts", "scene_audio", "manim", "image", "video", "render", "captions",
            "poster"} <= set(PUBLIC_ASSET_KINDS)  # the classic kinds, unchanged
    assert not set(PUBLIC_ASSET_KINDS) & set(PRIVATE_ASSET_KINDS)
    for kind in dynamic:
        assert storage_key_for(kind, "k", "audio/mpeg").startswith(f"assets/{kind}/k/")
    assert storage_key_for("calibration", "k", "audio/mpeg").startswith("private/calibration/k/")
    for rule in FIELD_RULES.values():  # every kind a field accepts can be served to the player
        assert rule.kinds <= set(PUBLIC_ASSET_KINDS), rule


@pytest.mark.parametrize(
    ("field", "kind", "mime", "ok"),
    [
        ("ClipScene.clip.asset_key", "clip", "video/mp4", True),
        ("ClipScene.clip.asset_key", "upload", "video/webm", True),
        ("ClipScene.clip.asset_key", "video", "video/mp4", True),
        ("ClipScene.clip.asset_key", "upload", "image/png", False),  # an image is not a clip
        ("ClipScene.clip.asset_key", "manim", "video/mp4", False),
        ("Overlay.clip.asset_key", "clip_proxy", "video/mp4", False),  # proxies are for the editor only
        ("Presentation.music.asset_key", "music", "audio/mpeg", True),
        ("Presentation.music.asset_key", "upload", "audio/wav", True),
        ("Presentation.music.asset_key", "upload", "video/mp4", False),
        ("Presentation.music.asset_key", "tts", "audio/mpeg", False),  # someone's narration is not music
        ("Beat.voice_override_asset_key", "narration_upload", "audio/wav", True),
        ("Beat.voice_override_asset_key", "upload", "audio/wav", False),  # only normalised recordings
        ("Beat.voice_override_asset_key", "tts", "audio/mpeg", False),
        ("Beat.voice_override_asset_key", "narration_upload", "video/webm", False),
    ],
)
def test_field_rule_fits(field: str, kind: str, mime: str, ok: bool) -> None:
    assert FIELD_RULES[field].fits(kind, mime) is ok


# --- the AssetRef helper -----------------------------------------------------------------------------------


def test_record_asset_refs_is_idempotent_and_per_project(db_session) -> None:
    alice, bob = add_project(db_session, "alice"), add_project(db_session, "bob")
    assert record_asset_refs(db_session, alice.id, ["b", "a", "a", ""]) == 2
    assert record_asset_refs(db_session, alice.id, ["a", "b"]) == 0
    assert record_asset_refs(db_session, alice.id, []) == 0
    db_session.commit()
    assert refs(db_session, alice) == ["a", "b"]
    assert refs(db_session, bob) == []
    assert record_asset_refs(db_session, bob.id, iter(["a"])) == 1  # any iterable, e.g. a set or a generator
    db_session.commit()
    assert refs(db_session, bob) == ["a"]


def test_record_asset_refs_handles_many_keys(db_session) -> None:
    project = add_project(db_session, "alice")
    keys = [f"k{i:04d}" for i in range(1203)]  # three INSERT batches
    assert record_asset_refs(db_session, project.id, keys) == 1203
    assert record_asset_refs(db_session, project.id, keys[::-1]) == 0
    db_session.commit()
    assert len(refs(db_session, project)) == 1203


def test_record_asset_refs_does_not_commit(db_session) -> None:
    project = add_project(db_session, "alice")
    record_asset_refs(db_session, project.id, ["a"])
    db_session.rollback()  # a failed request leaves no grant behind
    assert refs(db_session, project) == []


def test_pipeline_name_delegates_to_the_helper(db_session) -> None:
    project = add_project(db_session, "alice")
    assert dbops.ensure_asset_refs(db_session, project.id, ["a", "b"]) == 2
    assert record_asset_refs(db_session, project.id, ["b", "c"]) == 1
    db_session.commit()
    assert refs(db_session, project) == ["a", "b", "c"]


# --- asset_uses ------------------------------------------------------------------------------------------


def test_classic_uses_are_unchanged() -> None:
    from aadhi.schemas.screenplay import Screenplay

    sp = screenplay_dict()
    sp["figures"] = [{"id": "f1", "caption": "Circuit", "asset_key": "fig-1"}]
    sp["scenes"][0]["side_panel"] = {"kind": "image", "override_asset_key": "panel-1", "rationale": "shows it"}
    uses = asset_uses(Screenplay.model_validate(sp))
    assert uses == [
        AssetUse(("figures", 0, "asset_key"), "fig-1", frozenset({"figure", "upload"}), ("image/",)),
        AssetUse(
            ("scenes", 0, "side_panel", "override_asset_key"),
            "panel-1",
            frozenset({"upload", "image", "video", "figure", "manim"}),
            ("image/", "video/"),
        ),
    ]
    assert asset_uses(Screenplay.model_validate(screenplay_dict())) == []


def test_dynamic_key_fields_are_found() -> None:
    uses = asset_uses(ns(dynamic_doc()))
    overlay = ("overlays", 0, "clip", "asset_key")
    found = [(u.loc, u.key, u.drop) for u in uses]
    assert found == [
        (("scenes", 0, "beats", 0, *overlay), "clip-bob", ("scenes", 0, "beats", 0, "overlays", 0)),
        (("scenes", 0, "beats", 0, "overlays", 1, "clip", "asset_key"), "clip-own",
         ("scenes", 0, "beats", 0, "overlays", 1)),
        (("scenes", 0, "beats", 0, "voice_override_asset_key"), "voice-bob", None),
        (("scenes", 0, "beats", 1, "voice_override_asset_key"), "voice-own", None),
        (("scenes", 1, "clip", "asset_key"), "clip-bob-2", ("scenes", 1)),
        (("scenes", 1, "beats", 0, *overlay), "clip-bob", ("scenes", 1, "beats", 0, "overlays", 0)),
        (("scenes", 3, "reveal_beats", 0, *overlay), "clip-own",
         ("scenes", 3, "reveal_beats", 0, "overlays", 0)),
        (("presentation", "music", "asset_key"), "music-bob", None),
    ]
    assert uses[0].kinds == frozenset({"clip", "upload", "video"}) and uses[0].mime_prefixes == ("video/",)
    assert uses[4].kinds == frozenset({"clip", "upload", "video"}) and uses[4].mime_prefixes == ("video/",)
    assert uses[2].kinds == frozenset({"narration_upload"}) and uses[2].mime_prefixes == ("audio/",)
    assert uses[-1].kinds == frozenset({"music", "upload"}) and uses[-1].mime_prefixes == ("audio/",)


def _models_under(root: type) -> set[type]:
    """Every pydantic model class reachable from ``root`` through its field annotations."""
    from typing import get_args

    from pydantic import BaseModel

    seen: set[type] = set()
    stack: list[Any] = [root]
    while stack:
        tp = stack.pop()
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            if tp in seen:
                continue
            seen.add(tp)
            stack.extend(f.annotation for f in tp.model_fields.values())
        else:
            stack.extend(get_args(tp))
    return seen


def test_every_key_field_of_the_schema_is_authorised() -> None:
    """A new asset-key field must join ``FIELD_RULES`` and ``asset_uses`` (they read the Dynamic fields by name, so
    a renamed or added field would otherwise escape authorisation)."""
    from aadhi.schemas.screenplay import Screenplay

    key_fields = {
        (cls.__name__, name)
        for cls in _models_under(Screenplay)
        for name in cls.model_fields
        if "asset_key" in name
    }
    assert key_fields == {
        ("SourceFigure", "asset_key"),
        ("SidePanel", "override_asset_key"),
        ("SimulationScene", "override_asset_key"),
        ("AIVideoScene", "override_asset_key"),
        ("InteractiveScene", "poster_override_asset_key"),
        ("ClipRange", "asset_key"),  # ClipScene.clip and Overlay.clip
        ("MusicChoice", "asset_key"),  # Presentation.music
        ("Beat", "voice_override_asset_key"),
    }


def test_asset_uses_covers_every_rule_on_the_maximal_dynamic_document() -> None:
    """The transform-survival document (every v3 field) names a key in every field of the table; stripping all of
    them leaves a valid screenplay that names none."""
    import json
    import pathlib

    from aadhi.schemas.screenplay import Screenplay

    path = pathlib.Path(__file__).parent / "fixtures" / "transform_survival_dynamic.json"
    sp = Screenplay.model_validate(json.loads(path.read_text(encoding="utf-8")))
    uses = asset_uses(sp)
    rule_of = {(u.kinds, u.mime_prefixes) for u in uses}
    assert rule_of == {(r.kinds, r.mime_prefixes) for r in FIELD_RULES.values()}
    tails = {u.loc[-2:] if u.loc[-2] in ("clip", "music") else u.loc[-1:] for u in uses}
    assert {("clip", "asset_key"), ("music", "asset_key"), ("voice_override_asset_key",)} <= tails
    assert any("overlays" in u.loc for u in uses) and any(u.drop == u.loc[:2] for u in uses)
    data = sp.model_dump(mode="json")
    assert len(strip_asset_uses(data, uses)) == len(uses)
    stripped = Screenplay.model_validate(data)
    assert asset_uses(stripped) == []
    assert len(stripped.scenes) == len(sp.scenes) - sum(1 for u in uses if u.drop == u.loc[:2])


def test_unset_dynamic_fields_name_no_keys() -> None:
    doc = dynamic_doc()
    doc["presentation"] = {"music": {"source": "procedural", "asset_key": None}}
    doc["scenes"] = [
        {"id": "s1", "type": "content", "side_panel": None,
         "beats": [{"id": "b1", "overlays": [], "voice_override_asset_key": None}]},
    ]
    assert asset_uses(ns(doc)) == []
    doc["presentation"] = None  # classic: no presentation at all
    assert asset_uses(ns(doc)) == []


# --- 422 on writes -----------------------------------------------------------------------------------------


def test_foreign_and_wrong_kind_dynamic_keys_are_refused(db_session) -> None:
    alice, bob = add_project(db_session, "alice"), add_project(db_session, "bob")
    add_asset(db_session, "clip-own", "clip", "video/mp4")
    add_asset(db_session, "voice-own", "narration_upload", "audio/wav")
    add_asset(db_session, "clip-bob", "clip", "video/mp4")
    add_asset(db_session, "clip-bob-2", "upload", "video/mp4")
    add_asset(db_session, "voice-bob", "narration_upload", "audio/wav")
    add_asset(db_session, "music-bob", "music", "audio/mpeg")
    record_asset_refs(db_session, alice.id, ["clip-own", "voice-own"])
    record_asset_refs(db_session, bob.id, ["clip-bob", "clip-bob-2", "voice-bob", "music-bob"])
    db_session.commit()
    sp = ns(dynamic_doc())

    errors = asset_key_errors(db_session, [alice.id], sp, prefix=("screenplay",))
    assert [(e["loc"], e["type"]) for e in errors] == [
        (["body", "screenplay", "scenes", 0, "beats", 0, "overlays", 0, "clip", "asset_key"], "asset_key.unknown"),
        (["body", "screenplay", "scenes", 0, "beats", 0, "voice_override_asset_key"], "asset_key.unknown"),
        (["body", "screenplay", "scenes", 1, "clip", "asset_key"], "asset_key.unknown"),
        (["body", "screenplay", "scenes", 1, "beats", 0, "overlays", 0, "clip", "asset_key"], "asset_key.unknown"),
        (["body", "screenplay", "presentation", "music", "asset_key"], "asset_key.unknown"),
    ]
    with pytest.raises(ApiException) as exc:
        authorize_asset_keys(db_session, alice.id, sp)
    assert exc.value.status_code == 422 and exc.value.code == "validation"

    # Bob may use his own keys, but not Alice's, and only in fields of the right kind.
    errors = asset_key_errors(db_session, [bob.id], sp)
    assert {tuple(e["loc"][1:]) for e in errors} == {
        ("scenes", 0, "beats", 0, "overlays", 1, "clip", "asset_key"),
        ("scenes", 0, "beats", 1, "voice_override_asset_key"),
        ("scenes", 3, "reveal_beats", 0, "overlays", 0, "clip", "asset_key"),
    }
    misplaced = copy.deepcopy(dynamic_doc())
    misplaced["scenes"] = misplaced["scenes"][2:3]
    misplaced["presentation"]["music"]["asset_key"] = "voice-bob"  # a recording is not a music bed
    misplaced["scenes"][0]["beats"] = [{"id": "x", "voice_override_asset_key": "music-bob"}]
    errors = asset_key_errors(db_session, [bob.id], ns(misplaced))
    assert [(e["loc"][1:], e["type"]) for e in errors] == [
        (["scenes", 0, "beats", 0, "voice_override_asset_key"], "asset_key.kind"),
        (["presentation", "music", "asset_key"], "asset_key.kind"),
    ]
    assert "'music'" in errors[0]["msg"] and "audio/mpeg" in errors[0]["msg"]


# --- import strip ------------------------------------------------------------------------------------------


def test_strip_clears_optional_keys_and_removes_overlays_and_clip_scenes() -> None:
    data = dynamic_doc()
    refused = [u for u in asset_uses(ns(data)) if not u.key.endswith("-own")]
    warnings = strip_asset_uses(data, refused)
    assert warnings == [
        "Removed unavailable media reference at scenes.0.beats.0.overlays.0.clip.asset_key (the overlay was removed)",
        "Removed unavailable media reference at scenes.0.beats.0.voice_override_asset_key",
        "Removed unavailable media reference at scenes.1.clip.asset_key (the clip scene was removed)",
        "Removed unavailable media reference at scenes.1.beats.0.overlays.0.clip.asset_key (the overlay was removed)",
        "Removed unavailable media reference at presentation.music.asset_key",
    ]
    assert [s["id"] for s in data["scenes"]] == ["s1", "s2", "q1"]
    beat = data["scenes"][0]["beats"][0]
    assert [o["id"] for o in beat["overlays"]] == ["o2"]
    assert beat["voice_override_asset_key"] is None
    assert data["scenes"][0]["beats"][1]["voice_override_asset_key"] == "voice-own"
    assert data["chapters"][0]["scene_ids"] == ["s1", "s2", "q1"]
    assert data["scenes"][1]["look"]["continues_from"] is None  # its source scene is gone
    assert [o["id"] for o in data["scenes"][2]["reveal_beats"][0]["overlays"]] == ["o4"]
    assert data["presentation"]["music"] == {"source": "library", "asset_key": None}
    assert asset_uses(ns(data)) and all(u.key.endswith("-own") for u in asset_uses(ns(data)))


def test_strip_removes_several_items_of_one_list_from_the_end() -> None:
    data = dynamic_doc()
    beat = data["scenes"][0]["beats"][0]
    beat["overlays"] = [
        {"id": f"o{i}", "clip": {"asset_key": key, "in_frame": 0, "out_frame": 30}}
        for i, key in enumerate(["x1", "own-a", "x2", "x3", "own-b"])
    ]
    data["scenes"].insert(0, copy.deepcopy(data["scenes"][1]) | {"id": "c0"})
    data["chapters"][0]["scene_ids"].insert(0, "c0")
    refused = [u for u in asset_uses(ns(data)) if u.key in {"x1", "x2", "x3", "clip-bob-2"}]
    assert len(strip_asset_uses(data, refused)) == 5
    assert [s["id"] for s in data["scenes"]] == ["s1", "s2", "q1"]
    assert [o["id"] for o in data["scenes"][0]["beats"][0]["overlays"]] == ["o1", "o4"]
    assert data["chapters"][0]["scene_ids"] == ["s1", "s2", "q1"]


def test_strip_of_classic_keys_is_unchanged() -> None:
    data = {"figures": [{"id": "f1", "asset_key": "fig"}], "scenes": [
        {"id": "s1", "side_panel": {"kind": "image", "override_asset_key": "pan"}, "beats": []}]}
    refused = asset_uses(ns(data))
    assert strip_asset_uses(data, refused) == [
        "Removed unavailable media reference at figures.0.asset_key",
        "Removed unavailable media reference at scenes.0.side_panel.override_asset_key",
    ]
    assert data == {"figures": [{"id": "f1", "asset_key": None}], "scenes": [
        {"id": "s1", "side_panel": {"kind": "image", "override_asset_key": None}, "beats": []}]}
