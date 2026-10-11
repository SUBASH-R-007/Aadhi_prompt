"""The ``render_video`` job's engine switch and its Dynamic branch (DYNAMIC_VIDEO_DESIGN §7.1, §7.12).

Classic timelines (no ``presentation``) never reach the motion engine; a Dynamic one never reaches the classic
compositor. ``build_timeline`` is replaced by a fixed Dynamic timeline here, so these tests do not depend on how
the timeline builder resolves looks (the stage area's tests cover that).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aadhi.compose import video as video_mod
from aadhi.compose.motion import job as job_mod
from aadhi.compose.motion.job import caption_modes
from aadhi.jobs.base import FatalJobError
from aadhi.storage.assets import Produced
from tests.helpers import FakeJobContext

from ..dbutil import create_rows, get_render
from ..factories import make_manifest, make_screenplay
from .fixtures import AUDIO_KEY, CLIP_KEY, TOKEN, dynamic_timeline, make_bits_video, make_wav
from .stage_server import serve_stage


def _dynamic(version_id: int, **kw):
    tl = dynamic_timeline([2.0, 2.0], **kw)
    return tl.model_copy(update={"version_id": version_id, "screenplay_revision": 3})


def _patch_timeline(monkeypatch: pytest.MonkeyPatch, timeline_for) -> None:
    def fake_build(screenplay, manifest, *, settings, version_id=None, revision=None, include_intro=True, **kw):
        return timeline_for(version_id)

    monkeypatch.setattr(video_mod, "build_timeline", fake_build)


def test_caption_modes() -> None:
    tl = dynamic_timeline([2.0])
    assert caption_modes({}, tl) == (False, True)  # clean master + the default-on soft track
    assert caption_modes({"burn_captions": True}, tl) == (True, False)
    assert caption_modes({"soft_subtitles": False}, tl) == (False, False)
    burned = tl.model_copy(update={"presentation": tl.presentation.model_copy(update={"captions_burn": True})})
    assert caption_modes({}, burned) == (True, False)  # the presentation's choice
    assert caption_modes({"burn_captions": False}, burned) == (False, True)  # the request wins
    silent = dynamic_timeline([2.0], captions=False)
    assert caption_modes({"burn_captions": True}, silent) == (False, False)


def test_panel_warnings_say_what_the_dynamic_video_does() -> None:
    """The Dynamic stage lays a scene out without a medium it cannot show; the classic wording ("only its title is
    shown") is replaced, every other warning is kept as it is."""
    items = [{"scene_id": "s1", "reason": "panel_not_in_video", "message": "classic"},
             {"scene_id": "s2", "reason": "panel_media_missing", "message": "classic"},
             {"scene_id": "s3", "reason": "no_audio", "message": "silent"}]
    out = job_mod.dynamic_warnings(items)
    assert [o["message"] for o in out[:2]] == [job_mod.DYNAMIC_MESSAGES["panel_not_in_video"],
                                               job_mod.DYNAMIC_MESSAGES["panel_media_missing"]]
    assert "title" not in out[0]["message"] and out[2] == items[2] and items[0]["message"] == "classic"


def test_classic_timeline_never_reaches_the_motion_engine(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    calls: list[str] = []

    async def classic(ctx, job, payload, ws):
        calls.append("classic")
        assert job.timeline.presentation is None
        return {"engine": "classic"}

    async def motion(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("a classic timeline reached the motion engine")

    monkeypatch.setattr(video_mod, "_render", classic)
    monkeypatch.setattr(job_mod, "render_dynamic", motion)
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    assert asyncio.run(video_mod.render_video(ctx)) == {"engine": "classic"} and calls == ["classic"]


def test_dynamic_timeline_takes_the_motion_engine(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    _patch_timeline(monkeypatch, lambda vid: _dynamic(vid))

    async def classic(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("a Dynamic timeline reached the classic compositor")

    seen: list[object] = []

    async def motion(ctx, job, payload, ws):
        seen.append(job.timeline.presentation.style)
        return {"engine": "motion"}

    monkeypatch.setattr(video_mod, "_render", classic)
    monkeypatch.setattr(job_mod, "render_dynamic", motion)
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    assert asyncio.run(video_mod.render_video(ctx)) == {"engine": "motion"} and seen == ["dynamic"]


def test_dynamic_render_needs_the_admin_flag(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    _patch_timeline(monkeypatch, lambda vid: _dynamic(vid))
    assert app_env.dynamic_enabled is False  # DYNAMIC_ENABLED defaults to off
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    with pytest.raises(FatalJobError) as ei:
        asyncio.run(video_mod.render_video(ctx))
    assert ei.value.code == "dynamic_disabled"
    assert get_render(rows.render_id).status == "failed"


# --- the whole Dynamic job against the stub stage (slow) ----------------------------------------------


def _stored_media(asset_store, tmp: Path, ffmpeg: str) -> None:
    wav = make_wav(tmp / "narration.wav", 1.0)
    asset_store.put(AUDIO_KEY, "scene_audio", Produced(path=wav, mime="audio/wav", duration_s=1.0))
    clip = make_bits_video(ffmpeg, tmp / "clip.mp4", 60)
    asset_store.put(CLIP_KEY, "video", Produced(path=clip, mime="video/mp4", duration_s=2.0, width=320, height=180))


def _job_settings(app_env, base: str, tmp: Path):
    web = tmp / "web"
    (web / "js").mkdir(parents=True, exist_ok=True)
    return app_env.model_copy(update={
        "base_url": base, "dynamic_enabled": True, "render_bgm": False, "render_slot_dir": str(tmp / "slots"),
        "render_cpu_slots": 4, "render_motion_workers": 2, "render_min_free_gb": 0.0, "web_dir": web})


@pytest.mark.slow
def test_dynamic_job_renders_stores_and_records_qa(app_env, asset_store, monkeypatch, tmp_path: Path) -> None:
    from aadhi.compose.capabilities import chromium_installed, resolve_tool

    ffmpeg = resolve_tool(app_env.ffmpeg_path)
    if ffmpeg is None or chromium_installed() is False:
        pytest.skip("ffmpeg or Chromium missing")
    _stored_media(asset_store, tmp_path, ffmpeg)
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    _patch_timeline(monkeypatch, lambda vid: _dynamic(vid, media_scene=1))
    monkeypatch.setattr(video_mod, "_mint_token", lambda *a, **k: TOKEN)
    boxes = [{"x": 300, "y": 200, "w": 600, "h": 80, "role": "body", "critical": True, "ink": "#ffffff",
              "bg": "#202020", "overflow": False}]
    with serve_stage("not-the-token", {}, config={"boxes": boxes}) as (base, state):
        settings = _job_settings(app_env, base, tmp_path)
        ctx = FakeJobContext(settings=settings, assets=asset_store, project_id=rows.project_id,
                             payload={"render_id": rows.render_id})
        result = asyncio.run(video_mod.render_video(ctx))
    assert not any(r.startswith("/api/render/timeline") for r in state.requests)  # the worker served it
    render = get_render(rows.render_id)
    assert render.status == "succeeded" and result["engine"] == "motion"
    assert render.video_asset_key == result["video_asset_key"] and render.duration_s == pytest.approx(4.0, abs=0.05)
    qa = render.options["qa"]
    assert qa["ok"] is True and qa["engine"] == "motion" and qa["frames"] == qa["expected_frames"] == 120
    assert qa["preflight"]["ok"] is True and qa["preflight"]["sampled"] > 0
    assert qa["motion"]["chunks"] == 1 and qa["motion"]["workers"] == 1  # 120 frames: one chunk, one worker
    assert qa["subtitle_tracks"] == 1  # a clean file: the default-on caption track
    video = asset_store.get(result["video_asset_key"])
    assert video.kind == "render" and video.mime == "video/mp4" and (video.meta or {}).get("engine") == "motion"
    stages = [e["stage"] for e in ctx.events if e["type"] == "progress"]
    assert stages[0] == "preflight" and "capture" in stages and stages[-1] == "done"


@pytest.mark.slow
def test_preflight_block_stops_before_capture(app_env, asset_store, monkeypatch, tmp_path: Path) -> None:
    from aadhi.compose.capabilities import chromium_installed, resolve_tool

    ffmpeg = resolve_tool(app_env.ffmpeg_path)
    if ffmpeg is None or chromium_installed() is False:
        pytest.skip("ffmpeg or Chromium missing")
    _stored_media(asset_store, tmp_path, ffmpeg)
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    _patch_timeline(monkeypatch, lambda vid: _dynamic(vid))
    monkeypatch.setattr(video_mod, "_mint_token", lambda *a, **k: TOKEN)
    captured: list[object] = []

    async def no_capture(*a, **k):  # pragma: no cover - must not run
        captured.append(1)
        raise AssertionError("captured despite a blocking preflight")

    monkeypatch.setattr(job_mod, "render_motion", no_capture)
    boxes = [{"x": 10, "y": 10, "w": 600, "h": 80, "role": "body", "critical": True, "ink": "#ffffff",
              "bg": "#202020", "overflow": 40}]
    with serve_stage("not-the-token", {}, config={"boxes": boxes}) as (base, _):
        settings = _job_settings(app_env, base, tmp_path)
        ctx = FakeJobContext(settings=settings, assets=asset_store, payload={"render_id": rows.render_id})
        with pytest.raises(FatalJobError) as ei:
            asyncio.run(video_mod.render_video(ctx))
    assert ei.value.code == "preflight_blocked" and not captured
    render = get_render(rows.render_id)
    assert render.status == "failed"
    qa = render.options["qa"]
    assert qa["ok"] is False and qa["stage"] == "preflight" and qa["preflight"]["ok"] is False
