"""Asset authorisation of the Dynamic key fields end to end (design §6.7, §8.10; B0): clip scenes, overlays, the
music bed and the teacher's own narration. ``PUT /screenplay`` refuses another teacher's key (422) and a key of the
wrong kind; import strips them (an overlay or a clip scene whose clip is unavailable is removed) and authorises the
keys it keeps; the upload route records the ``AssetRef`` through the shared helper.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from sqlalchemy import select

from aadhi.models import AssetRef, Job
from tests.api.factories import add_asset_ref, add_project, screenplay_dict, tiny_png


@pytest.fixture()
def dyn(api):
    """The API with every DYNAMIC_FEATURES write gate open: the features ship in later batches, their keys are
    authorised from B0 (design §6.7: "tests enable the flags")."""
    from aadhi.schemas.features import DYNAMIC_FEATURES

    api.settings.dynamic_features = list(DYNAMIC_FEATURES)
    return api


PRESENTATION = {
    "look_seed": 7,
    "theme_family": "teal_coral",
    "personality": "calm",
    "display_font": "outfit",
    "caption_style": "karaoke",
    "cue_term_style": "marker",
    "hook_kind": "question",
    "asset_style": {"family": "teal_coral", "personality": "calm"},
    "voices": {"narrator": "en-in-narrator-1"},
}


def dynamic_sp(
    *, clip: str | None = None, overlay: str | None = None, voice: str | None = None, music: str | None = None
) -> dict[str, Any]:
    """A screenplay naming the given keys: a clip scene after s1, an overlay and the teacher's narration on s1's
    first beat, a music bed."""
    sp = screenplay_dict()
    sp["chapters"] = [{"id": "ch1", "title": "Part 1", "scene_ids": [s["id"] for s in sp["scenes"]]}]
    beat = sp["scenes"][0]["beats"][0]
    if overlay:
        beat["overlays"] = [
            {"id": "o1", "at_word": 2, "at_text": "scene",
             "clip": {"asset_key": overlay, "in_frame": 0, "out_frame": 60}}
        ]
    if voice:
        beat["voice_override_asset_key"] = voice
    if clip:
        clip_range = {"asset_key": clip, "in_frame": 0, "out_frame": 90}
        sp["scenes"].insert(1, {"id": "c1", "type": "clip", "title": "Lab demo", "clip": clip_range})
        sp["chapters"][0]["scene_ids"].insert(1, "c1")
    if music:
        sp["presentation"] = {**PRESENTATION, "music": {"source": "library", "asset_key": music}}
    return sp


FULL = {"clip": "clip-a", "overlay": "clip-a", "voice": "voice-a", "music": "music-a"}
LOCS = {
    "clip": ["scenes", 1, "clip", "asset_key"],
    "overlay": ["scenes", 0, "beats", 0, "overlays", 0, "clip", "asset_key"],
    "voice": ["scenes", 0, "beats", 0, "voice_override_asset_key"],
    "music": ["presentation", "music", "asset_key"],
}


def seed(api):
    """Alice's lesson with her own clip, recording and music; Bob's project with his own copies."""
    alice, c = api.editor("alice")
    bob = api.user("bob")
    with api.db() as db:
        project, version = add_project(db, alice)
        other, _ = add_project(db, bob)
        add_asset_ref(db, project, "clip-a", kind="clip", mime="video/mp4")
        add_asset_ref(db, project, "voice-a", kind="narration_upload", mime="audio/wav")
        add_asset_ref(db, project, "music-a", kind="music", mime="audio/mpeg")
        add_asset_ref(db, project, "image-a", kind="upload", mime="image/png")
        add_asset_ref(db, project, "upload-video-a", kind="upload", mime="video/mp4")
        add_asset_ref(db, project, "upload-audio-a", kind="upload", mime="audio/mpeg")
        add_asset_ref(db, other, "clip-b", kind="clip", mime="video/mp4")
        add_asset_ref(db, other, "voice-b", kind="narration_upload", mime="audio/wav")
        add_asset_ref(db, other, "music-b", kind="music", mime="audio/mpeg")
    return c, project, version


def put(c, vid: int, sp: dict[str, Any], revision: int):
    return c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": sp, "revision": revision})


def test_own_dynamic_keys_are_accepted(dyn) -> None:
    c, _, version = seed(dyn)
    r = put(c, version.id, dynamic_sp(**FULL), 1)
    assert r.status_code == 200, r.text
    stored = c.get(f"/api/versions/{version.id}").json()["screenplay"]
    assert stored["scenes"][1]["clip"]["asset_key"] == "clip-a"
    assert stored["scenes"][0]["beats"][0]["overlays"][0]["clip"]["asset_key"] == "clip-a"
    assert stored["scenes"][0]["beats"][0]["voice_override_asset_key"] == "voice-a"
    assert stored["presentation"]["music"]["asset_key"] == "music-a"
    # A teacher's own uploads fit too: a video upload as a clip, an audio upload as music.
    r = put(c, version.id, dynamic_sp(clip="upload-video-a", overlay="upload-video-a", music="upload-audio-a"), 2)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("field", ["clip", "overlay", "voice", "music"])
def test_another_teachers_key_is_refused(dyn, field: str) -> None:
    c, _, version = seed(dyn)
    foreign = {"clip": "clip-b", "overlay": "clip-b", "voice": "voice-b", "music": "music-b"}[field]
    r = put(c, version.id, dynamic_sp(**{field: foreign}), 1)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "validation"
    assert [(e["loc"], e["type"]) for e in r.json()["detail"]] == [
        (["body", "screenplay", *LOCS[field]], "asset_key.unknown")
    ]
    unknown = put(c, version.id, dynamic_sp(**{field: "never-uploaded"}), 1)
    assert unknown.status_code == 422
    assert c.get(f"/api/versions/{version.id}").json()["revision"] == 1  # nothing was saved


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("clip", "image-a"),  # a picture is not a clip
        ("overlay", "voice-a"),
        ("voice", "upload-audio-a"),  # only a normalised recording replaces the narration
        ("voice", "music-a"),
        ("music", "voice-a"),  # nor is a recording a music bed
        ("music", "upload-video-a"),
    ],
)
def test_own_key_of_the_wrong_kind_is_refused(dyn, field: str, key: str) -> None:
    c, _, version = seed(dyn)
    r = put(c, version.id, dynamic_sp(**{field: key}), 1)
    assert r.status_code == 422, r.text
    assert [(e["loc"], e["type"]) for e in r.json()["detail"]] == [
        (["body", "screenplay", *LOCS[field]], "asset_key.kind")
    ]


def test_preview_authorises_dynamic_keys(dyn) -> None:
    c, _, version = seed(dyn)
    r = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": dynamic_sp(music="music-b")})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "screenplay", *LOCS["music"]]


def _import(c, sp: dict[str, Any]):
    return c.post("/api/projects/import", files={"file": ("lecture.json", json.dumps(sp).encode(), "application/json")})


def test_import_strips_another_teachers_dynamic_keys(dyn) -> None:
    seed(dyn)
    bob = dyn.login("bob")
    sp = dynamic_sp(**FULL)
    sp["scenes"][2]["look"] = {"continues_from": "c1"}  # s2 continued the clip scene's last frame
    r = _import(bob, sp)
    assert r.status_code == 201, r.text
    warnings = [w for w in r.json()["warnings"] if w.startswith("Removed unavailable media reference")]
    assert warnings == [
        "Removed unavailable media reference at scenes.0.beats.0.overlays.0.clip.asset_key (the overlay was removed)",
        "Removed unavailable media reference at scenes.0.beats.0.voice_override_asset_key",
        "Removed unavailable media reference at scenes.1.clip.asset_key (the clip scene was removed)",
        "Removed unavailable media reference at presentation.music.asset_key",
    ]
    vid = r.json()["version"]["id"]
    stored = bob.get(f"/api/versions/{vid}").json()["screenplay"]
    assert [s["id"] for s in stored["scenes"]] == ["s1", "s2", "q1"]
    assert stored["chapters"][0]["scene_ids"] == ["s1", "s2", "q1"]
    beat = stored["scenes"][0]["beats"][0]
    assert "overlays" not in beat and "voice_override_asset_key" not in beat  # gone, so left out of the JSON
    assert stored["presentation"]["music"].get("asset_key") is None
    assert stored["scenes"][1].get("look", {}).get("continues_from") is None
    with dyn.db() as db:
        project_id = r.json()["project"]["id"]
        assert db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project_id)).scalars().all() == []


def test_import_keeps_the_teachers_own_keys_and_authorises_them(dyn) -> None:
    c, _, _ = seed(dyn)
    sp = dynamic_sp(**FULL)
    r = _import(c, sp)
    assert r.status_code == 201, r.text
    assert not [w for w in r.json()["warnings"] if w.startswith("Removed unavailable media reference")]
    project_id, vid = r.json()["project"]["id"], r.json()["version"]["id"]
    with dyn.db() as db:
        keys = sorted(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project_id)).scalars())
    assert keys == ["clip-a", "music-a", "voice-a"]
    detail = c.get(f"/api/versions/{vid}").json()
    assert detail["screenplay"]["scenes"][1]["clip"]["asset_key"] == "clip-a"
    # The new project may save them (its own AssetRef rows), but still nothing of Bob's.
    with dyn.db() as db:  # the import's build job is not run here; let the version be edited
        db.get(Job, r.json()["job"]["id"]).status = "succeeded"
        db.commit()
    again = put(c, vid, detail["screenplay"], detail["revision"])
    assert again.status_code == 200, again.text
    stolen = copy.deepcopy(detail["screenplay"])
    stolen["presentation"]["music"]["asset_key"] = "music-b"
    refused = put(c, vid, stolen, detail["revision"] + 1)
    assert refused.status_code == 422


def test_import_strips_own_keys_of_the_wrong_kind(dyn) -> None:
    c, _, _ = seed(dyn)
    r = _import(c, dynamic_sp(clip="image-a", music="voice-a"))
    assert r.status_code == 201, r.text
    assert len([w for w in r.json()["warnings"] if w.startswith("Removed unavailable media reference")]) == 2
    stored = c.get(f"/api/versions/{r.json()['version']['id']}").json()["screenplay"]
    assert [s["id"] for s in stored["scenes"]] == ["s1", "s2", "q1"]
    assert stored["presentation"]["music"].get("asset_key") is None


def test_upload_records_the_asset_ref_once(dyn) -> None:
    c, project, _ = seed(dyn)
    for _ in range(2):  # the same bytes twice: one AssetRef row
        r = c.post(
            "/api/uploads",
            files={"file": ("dot.png", tiny_png(), "image/png")},
            data={"purpose": "figure", "project_id": str(project.id)},
        )
        assert r.status_code == 201, r.text
    key = r.json()["asset_key"]
    with dyn.db() as db:
        rows = db.execute(
            select(AssetRef.id).where(AssetRef.project_id == project.id, AssetRef.asset_key == key)
        ).scalars().all()
    assert len(rows) == 1


def _many_keys(n_scenes: int) -> dict[str, Any]:
    """A screenplay naming five distinct keys on every beat (4 overlays and the teacher's narration), 40 beats a
    scene: from 164 scenes on, more keys than SQLite binds in one statement (32766)."""
    sp = screenplay_dict()
    for s in range(n_scenes):
        beats = []
        for b in range(40):
            overlays = [{"id": f"o{b}-{o}", "at_word": 0, "at_text": "",
                         "clip": {"asset_key": f"k-{s}-{b}-{o}", "in_frame": 0, "out_frame": 30}} for o in range(4)]
            beats.append({"id": f"m{s}-b{b}", "narration": "Look here.", "overlays": overlays,
                          "voice_override_asset_key": f"v-{s}-{b}"})
        sp["scenes"].append({"id": f"m{s}", "type": "content", "title": "More", "board": [], "beats": beats})
    return sp


def test_many_dynamic_keys_are_checked_in_batches(dyn) -> None:
    """Design §6.7: tens of thousands of keys are a 422 (or stripped on import), never a 500 from SQLite's
    bind-variable limit; keys the project owns are still found in any batch, through any group of projects."""
    from aadhi.api.screenplay_service import asset_key_errors, strip_unauthorized_asset_keys
    from aadhi.schemas.screenplay import Screenplay

    c, project, version = seed(dyn)
    sp = _many_keys(165)
    sp["scenes"][-1]["beats"][-1]["overlays"][0]["clip"]["asset_key"] = "clip-a"  # Alice's own clip, in the last batch
    model = Screenplay.model_validate(sp)
    with dyn.db() as db:
        errors = asset_key_errors(db, [project.id], model)
        assert len(errors) == 165 * 40 * 5 - 1 > 32766
        assert {e["type"] for e in errors} == {"asset_key.unknown"}
        # an admin's import checks every project: the project ids are batched too
        stripped, warnings, kept = strip_unauthorized_asset_keys(db, model, project_ids=[*range(10_000, 11_200),
                                                                                      project.id])
        assert kept == {"clip-a"} and len(warnings) == len(errors)
        assert stripped.scenes[-1].beats[-1].overlays[0].clip.asset_key == "clip-a"
    r = put(c, version.id, sp, 1)
    assert r.status_code == 422, r.text[:300]
    assert r.json()["code"] == "validation" and len(r.json()["detail"]) == len(errors)
