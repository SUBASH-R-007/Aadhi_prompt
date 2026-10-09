"""Presenter fields of the timeline: the narration loudness envelope and the cue-bubble switch."""

from __future__ import annotations

import json

from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.timeline import Timeline

from .factories import make_manifest, make_screenplay


def settings(**kw) -> Settings:
    return Settings(_env_file=None, render_fps=30, **kw)


def test_envelope_is_copied_from_the_narration_and_absent_without_one() -> None:
    sp = make_screenplay()
    manifest = make_manifest(sp)
    sid = next(iter(manifest.audio))
    manifest.audio[sid] = manifest.audio[sid].model_copy(update={"envelope": "AAEC/w==", "envelope_fps": 25})
    tl = build_timeline(sp, manifest, settings=settings())
    for s in tl.scenes:
        if s.scene_id == sid:
            assert (s.audio_envelope, s.audio_envelope_fps) == ("AAEC/w==", 25)
        else:
            assert s.audio_envelope is None, "manifests built before the envelope existed"
    data = json.loads(tl.model_dump_json())
    by_id = {s["scene_id"]: s for s in data["scenes"]}
    assert by_id[sid]["audio_envelope"] == "AAEC/w==" and by_id[sid]["audio_envelope_fps"] == 25
    assert all("audio_envelope" not in s for k, s in by_id.items() if k != sid), "absent fields stay out of the JSON"
    assert Timeline.model_validate(data).scenes[[s.scene_id for s in tl.scenes].index(sid)].audio_envelope == "AAEC/w=="


def test_envelope_follows_the_narration_file_only() -> None:
    """An editor preview whose beats no longer match the narration has no audio, hence no envelope."""
    sp = make_screenplay()
    manifest = make_manifest(sp)
    for sid in list(manifest.audio):
        manifest.audio[sid] = manifest.audio[sid].model_copy(update={"envelope": "AAE="})
    data = sp.model_dump(mode="json")
    scene = next(s for s in data["scenes"] if s["id"] in manifest.audio and s.get("beats"))
    scene["beats"][0]["narration"] = "Completely different words are spoken here now."
    edited = type(sp).model_validate(data)
    tl = preview_timeline(edited, manifest, settings=settings())
    changed = next(s for s in tl.scenes if s.scene_id == scene["id"])
    assert changed.audio_asset_key is None and changed.audio_envelope is None


def test_cue_bubble_switch() -> None:
    sp = make_screenplay()
    on = build_timeline(sp, make_manifest(sp), settings=settings())
    assert all(s.layout.mascot_cues for s in on.scenes)
    assert all("mascot_cues" not in s["layout"] for s in json.loads(on.model_dump_json())["scenes"])
    off = build_timeline(sp, make_manifest(sp), settings=settings(mascot_cues=False))
    assert all(s.layout.mascot_cues is False for s in off.scenes)
    assert all(s["layout"]["mascot_cues"] is False for s in json.loads(off.model_dump_json())["scenes"])
