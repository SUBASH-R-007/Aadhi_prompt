"""Hidden scenes and the minimum scene duration on the Timeline (``SceneBase.hidden`` / ``min_seconds``).

One Timeline drives the live player, the editor preview and the MP4 (``render_video`` builds it with
``build_timeline``), so these timeline-level checks cover all three; ``segment_frames`` is the MP4's own cut.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aadhi.compose.timeline import STORAGE_KEYS_META, build_timeline, preview_timeline, timeline_asset_keys
from aadhi.compose.video import segment_frames
from aadhi.config import Settings
from aadhi.jobs.base import FatalJobError
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline
from tests.helpers import FakeJobContext

from .dbutil import create_rows, get_render
from .factories import beat, make_manifest, make_screenplay, screenplay_dict


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_fps=30)


def edited(edits: dict[str, dict[str, Any]], **top: Any) -> Screenplay:
    data = screenplay_dict()
    data.update(top)
    for s in data["scenes"]:
        s.update(edits.get(s["id"], {}))
    return Screenplay.model_validate(data)


def build(sp: Screenplay, settings: Settings, **kw: Any) -> Timeline:
    return build_timeline(sp, make_manifest(sp), settings=settings, **kw)


def scene(tl: Timeline, sid: str):
    return next(s for s in tl.scenes if s.scene_id == sid)


# --- defaults: nothing changes for lectures that use neither -------------------------------------


def test_defaults_are_left_out_of_the_json_and_change_no_output(settings: Settings) -> None:
    plain = make_screenplay()
    explicit = edited({s["id"]: {"hidden": False, "min_seconds": None} for s in screenplay_dict()["scenes"]})
    dumped = json.dumps(explicit.model_dump(mode="json"))
    assert explicit.model_dump(mode="json") == plain.model_dump(mode="json")
    assert '"hidden":' not in dumped and '"min_seconds":' not in dumped
    a, b = build(plain, settings).model_dump(mode="json"), build(explicit, settings).model_dump(mode="json")
    assert a == b and all("hold_seconds" not in s for s in a["scenes"])


def test_a_minimum_shorter_than_the_scene_changes_nothing(settings: Settings) -> None:
    ref = build(make_screenplay(), settings)
    short = edited({"s-board": {"min_seconds": 1.0}, "s-chapter": {"min_seconds": 1.0}})
    assert build(short, settings).model_dump(mode="json") == ref.model_dump(mode="json")


def test_schema_bounds() -> None:
    from pydantic import ValidationError

    for bad in (0.5, 0, -3, 600.5, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            edited({"s-board": {"min_seconds": bad}})
    assert edited({"s-board": {"min_seconds": 600}}).scene_by_id("s-board").min_seconds == 600.0
    sp = edited({"s-board": {"hidden": True, "min_seconds": 12.5}})
    out = sp.model_dump(mode="json")["scenes"][2]
    assert out["hidden"] is True and out["min_seconds"] == 12.5
    assert Screenplay.model_validate(sp.model_dump(mode="json")) == sp


# --- hidden scenes -------------------------------------------------------------------------------


def test_hidden_scenes_are_left_out_of_the_timeline_captions_and_assets(settings: Settings) -> None:
    full = make_screenplay()
    ref = build(full, settings)
    sp = edited({"s-board": {"hidden": True}, "s-quiz": {"hidden": True}})
    tl = build(sp, settings)
    Timeline.model_validate(tl.model_dump(mode="json"))  # contiguous, valid
    shown = [s.id for s in full.scenes if s.id not in ("s-board", "s-quiz")]
    assert [s.scene_id for s in tl.scenes] == shown
    assert [s.index for s in tl.scenes] == list(range(len(shown)))
    for s in tl.scenes:  # every scene shown is exactly as in the full lecture, only earlier
        r = scene(ref, s.scene_id)
        assert s.duration == r.duration and s.beats == r.beats
    gone = scene(ref, "s-board").duration + scene(ref, "s-quiz").duration
    assert tl.total_duration == pytest.approx(ref.total_duration - gone, abs=1e-3)
    text = " ".join(c.text for c in tl.captions)
    assert "proportional" not in text and "quick question" not in text and "Power lines" in text
    assert len(tl.captions) == len(ref.captions) - sum(
        len(b.captions) for sid in ("s-board", "s-quiz") for b in scene(ref, sid).beats)
    assert all(any(s.start - 1e-3 <= c.start < s.start + s.duration for s in tl.scenes) for c in tl.captions)
    keys = timeline_asset_keys(tl)
    assert "scene_audio-s-board" not in keys and "scene_audio-s-quiz" not in keys and "figure-aaa" not in keys
    assert "figure-aaa" not in tl.meta[STORAGE_KEYS_META]


def test_the_preview_and_the_built_timeline_agree(settings: Settings) -> None:
    sp = edited({"s-board": {"hidden": True}, "s-video": {"min_seconds": 20}, "s-quiz": {"min_seconds": 40}})
    built = build(sp, settings)
    preview = preview_timeline(sp, make_manifest(sp), settings=settings)
    assert [(s.scene_id, s.start, s.duration, s.hold_seconds, s.audio_asset_key) for s in preview.scenes] == \
        [(s.scene_id, s.start, s.duration, s.hold_seconds, s.audio_asset_key) for s in built.scenes]
    assert preview.captions == built.captions and preview.chapters == built.chapters
    assert preview.total_duration == built.total_duration


def test_hidden_chapter_card_and_chapters_whose_scenes_are_all_hidden(settings: Settings) -> None:
    tl = build(edited({"s-chapter": {"hidden": True}}), settings)  # the only chapter card is hidden
    assert "Current" not in [c.title for c in tl.chapters]  # (chapters then follow the concepts)
    chapters = [{"id": "c1", "title": "Foundations", "scene_ids": ["s-title", "s-chapter"]},
                {"id": "c2", "title": "The Law", "scene_ids": ["s-board"]},
                {"id": "c3", "title": "Practice", "scene_ids": ["s-quiz"]}]
    shown = build(edited({}, chapters=chapters), settings)
    assert [c.title for c in shown.chapters] == ["Introduction", "Foundations", "The Law", "Practice"]
    tl2 = build(edited({"s-board": {"hidden": True}}, chapters=chapters), settings)
    assert [c.title for c in tl2.chapters] == ["Introduction", "Foundations", "Practice"]
    assert tl2.chapters[2].start == scene(tl2, "s-quiz").start


def test_every_scene_hidden_leaves_only_the_intro(settings: Settings) -> None:
    sp = edited({s["id"]: {"hidden": True} for s in screenplay_dict()["scenes"]})
    tl = build(sp, settings)
    assert tl.scenes == [] and tl.captions == [] and tl.total_duration == pytest.approx(tl.intro.duration)
    assert [c.title for c in tl.chapters] == ["Introduction"]
    Timeline.model_validate(tl.model_dump(mode="json"))


def test_the_render_job_refuses_a_lecture_whose_scenes_are_all_hidden(app_env, asset_store) -> None:
    import asyncio

    from aadhi.compose.video import render_video

    sp = edited({s["id"]: {"hidden": True} for s in screenplay_dict()["scenes"]})
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    with pytest.raises(FatalJobError) as ei:
        asyncio.run(render_video(ctx))
    assert ei.value.code == "empty" and "hidden" in str(ei.value)
    assert get_render(rows.render_id).status == "failed"


# --- minimum duration ----------------------------------------------------------------------------


def test_min_seconds_holds_the_scene_after_its_content(settings: Settings) -> None:
    ref = build(make_screenplay(), settings)
    board = scene(ref, "s-board")
    target = round(board.duration + 5.25, 3)
    tl = build(edited({"s-board": {"min_seconds": target}}), settings)
    Timeline.model_validate(tl.model_dump(mode="json"))
    held = scene(tl, "s-board")
    assert held.duration == pytest.approx(target) and held.hold_seconds == pytest.approx(5.25)
    # the content keeps its timing: beats, words, captions, sync cues, the panel and the audio
    assert held.beats == board.beats and held.sync_cues == board.sync_cues and held.side_panel == board.side_panel
    assert held.audio_asset_key == board.audio_asset_key and held.audio_duration == board.audio_duration
    for r, s in zip(ref.scenes, tl.scenes, strict=True):  # later scenes start 5.25 s later
        assert s.start == pytest.approx(r.start + (5.25 if r.index > board.index else 0.0), abs=1e-3)
    assert tl.total_duration == pytest.approx(ref.total_duration + 5.25, abs=1e-3)
    assert [c.text for c in tl.captions] == [c.text for c in ref.captions]
    assert {s["scene_id"] for s in tl.model_dump(mode="json")["scenes"] if "hold_seconds" in s} == {"s-board"}
    # the MP4 cuts the same scene from the same times
    fps = settings.render_fps
    frames = segment_frames(tl, fps)
    ref_frames = segment_frames(ref, fps)
    i = board.index + 1  # segment 0 is the intro
    assert frames[i][1] - frames[i][0] == pytest.approx(target * fps, abs=1)
    assert frames[-1][1] - ref_frames[-1][1] == pytest.approx(5.25 * fps, abs=1)


def test_min_seconds_on_a_silent_card_and_a_quiz(settings: Settings) -> None:
    ref = build(make_screenplay(), settings)
    quiz = scene(ref, "s-quiz")
    tl = build(edited({"s-chapter": {"min_seconds": 9}, "s-quiz": {"min_seconds": quiz.duration + 3}}), settings)
    card = scene(tl, "s-chapter")
    assert card.duration == pytest.approx(9.0) and card.beats == []
    held = scene(tl, "s-quiz")
    assert held.hold_seconds == pytest.approx(3.0) and held.quiz == quiz.quiz  # countdown and reveal unchanged


def test_sync_cues_ignore_the_hold(settings: Settings) -> None:
    def lecture(min_seconds: float | None) -> Screenplay:
        return Screenplay.model_validate({"language": "en-IN", "scenes": [
            {"id": "run", "type": "content", "title": "Run it",
             "board": [{"id": "c1", "kind": "code", "language": "python", "code": "print(2 + 3)"}],
             "beats": [beat("r1", "Here is a tiny program that adds two numbers.", board_item_id="c1"),
                       beat("r2", "When we run it, the program prints five.")],
             "side_panel": {"kind": "terminal", "rationale": "shows the output",
                            "terminal": {"command": "python add.py", "output": "5\ndone"}},
             **({"min_seconds": min_seconds} if min_seconds else {})},
            {"id": "look", "type": "content", "title": "Picture",
             "beats": [beat("l1", "Some words first."), beat("l2", "Now look at this diagram.")],
             "side_panel": {"kind": "figure", "figure_id": "fig", "rationale": "the part"},
             **({"min_seconds": min_seconds} if min_seconds else {})},
        ], "figures": [{"id": "fig", "caption": "A part"}]})

    plain = build_timeline(lecture(None), None, settings=settings, include_intro=False)
    held = build_timeline(lecture(60), None, settings=settings, include_intro=False)
    for a, b in zip(plain.scenes, held.scenes, strict=True):
        assert b.duration == pytest.approx(60.0) and b.hold_seconds == pytest.approx(60.0 - a.duration, abs=1e-3)
        assert b.sync_cues == a.sync_cues
        assert all(c.start < a.duration and (c.end is None or c.end <= a.duration) for c in b.sync_cues)
    kinds = {c.kind for s in held.scenes for c in s.sync_cues}
    assert {"output", "focus"} <= kinds


# --- the MP4 itself (slow: real ffmpeg + the stub render page) ----------------------------------


@pytest.mark.slow
def test_the_mp4_leaves_hidden_scenes_out_and_holds_the_scene(app_env, asset_store, tmp_path, monkeypatch) -> None:
    import asyncio

    from aadhi.compose import video as video_mod
    from aadhi.compose.ffmpeg import probe
    from aadhi.compose.timeline import resolve_urls
    from aadhi.config import ROOT_DIR

    from .stub_server import serve_stub
    from .test_video import _store_real_assets

    base_settings = app_env.model_copy(update={"render_width": 640, "render_height": 360, "render_fps": 15,
                                               "render_preset": "ultrafast", "render_crf": 30,
                                               "branding_dir": ROOT_DIR / "video_template"})
    plain = make_screenplay()
    ref = build_timeline(plain, make_manifest(plain), settings=base_settings, revision=3)
    hold = 4.0
    sp = edited({"s-quiz": {"hidden": True}, "s-video": {"hidden": True},
                 "s-title": {"min_seconds": scene(ref, "s-title").duration + hold}})
    manifest = _store_real_assets(asset_store, make_manifest(sp), tmp_path, base_settings)
    rows = create_rows(base_settings, sp, manifest)
    tl = build_timeline(sp, manifest, settings=base_settings, version_id=rows.version_id, revision=3)
    assert scene(tl, "s-title").hold_seconds == pytest.approx(hold)
    monkeypatch.setattr(video_mod, "_mint_token", lambda *a, **k: "render-token-hidden")
    with serve_stub("render-token-hidden", resolve_urls(tl, asset_store).model_dump(mode="json")) as (base, _state):
        settings = base_settings.model_copy(update={"base_url": base})
        ctx = FakeJobContext(settings=settings, assets=asset_store, version_id=rows.version_id,
                             project_id=rows.project_id,
                             payload={"render_id": rows.render_id, "burn_captions": False, "include_intro": True})
        result = asyncio.run(video_mod.render_video(ctx))

    render = get_render(rows.render_id)
    assert render.status == "succeeded"
    gone = scene(ref, "s-quiz").duration + scene(ref, "s-video").duration
    assert tl.total_duration == pytest.approx(ref.total_duration - gone + hold, abs=1e-3)
    path = asset_store.storage.local_path(asset_store.get(result["video_asset_key"]).storage_key)
    info = asyncio.run(probe(path, settings=settings))
    assert info.duration == pytest.approx(tl.total_duration, abs=0.3)  # the MP4 is exactly the timeline
    srt = asset_store.storage.get_bytes(asset_store.get(result["srt_asset_key"]).storage_key).decode()
    assert "quick question" not in srt and "Power lines" not in srt and "Welcome" in srt
