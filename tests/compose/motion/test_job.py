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
from aadhi.schemas.timeline import Timeline
from aadhi.storage.assets import Produced
from tests.helpers import FakeJobContext

from ..dbutil import create_rows, get_render
from ..dynamic.helpers import missing
from ..factories import make_manifest, make_screenplay
from .fixtures import AUDIO_KEY, CLIP_KEY, TOKEN, dynamic_timeline, make_bits_video, make_wav
from .stage_server import serve_stage


def _dynamic(version_id: int, *, panel: str | None = None, **kw):
    tl = dynamic_timeline([2.0, 2.0], **kw)
    if panel is not None:  # a side panel of this kind (PANELS) on the first scene
        data = tl.model_dump(mode="json")
        data["scenes"][0]["side_panel"] = {"panel": PANELS[panel], "media": None, "show_at": 0.0}
        tl = Timeline.model_validate(data)
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


PANELS = {
    "chart": {"kind": "chart", "chart": {"labels": ["a", "b"], "datasets": [{"label": "x", "data": [1, 2]}]}},
    "graph": {"kind": "graph", "graph": {"points": [{"x": 1, "y": 2}]}},
    "model_3d": {"kind": "model_3d", "model_3d": {"primitives": [{"shape": "sphere"}]}},
    "skill_tree": {"kind": "skill_tree"},
    "quiz": {"kind": "quiz", "quiz": {"question": "Which?", "options": ["a", "b"], "correct_index": 0}},
    "terminal": {"kind": "terminal", "terminal": {"command": "python ohm.py", "output": "V = 6"}},
    "image": {"kind": "image", "image_prompt": "a resistor"},  # its picture is missing (a classic warning)
}


def _with_panels(kinds: list[str], *, hidden: frozenset[int] = frozenset()) -> Timeline:
    data = dynamic_timeline([2.0] * len(kinds)).model_dump(mode="json")
    for i, kind in enumerate(kinds):
        data["scenes"][i]["side_panel"] = {"panel": PANELS[kind], "media": None, "show_at": 0.0}
        data["scenes"][i]["layout"] = {"show_side_panel": i not in hidden}
    return Timeline.model_validate(data)


def test_side_panels_the_stage_does_not_draw_are_reported() -> None:
    """The Dynamic stage draws a panel's picture or clip and the terminal card, and lays the scene out without any
    other panel (live and MP4): each such shown panel gets a render warning naming the scene, in scene order with the
    classic items; a panel the scene does not show is not reported."""
    kinds = ["chart", "terminal", "graph", "image", "model_3d", "skill_tree", "quiz", "chart"]
    tl = _with_panels(kinds, hidden=frozenset({7}))
    numbers = {f"s{i}": i + 3 for i in range(len(kinds))}
    items = job_mod.dynamic_warnings(video_mod.render_warnings(tl, numbers=numbers), tl, numbers=numbers)
    assert [(it["scene_index"], it["reason"]) for it in items] == [
        (0, "panel_not_drawn"), (2, "panel_not_drawn"), (3, "panel_media_missing"), (4, "panel_not_drawn"),
        (5, "panel_not_drawn"), (6, "panel_not_drawn")]
    chart = items[0]
    assert chart == {"scene_index": 0, "scene_id": "s0", "title": "Scene 0", "reason": "panel_not_drawn",
                     "blocking": False, "scene_number": 3,
                     "message": "This side panel (a chart) is not shown in Dynamic videos yet: the scene is laid out "
                                "without it."}
    assert [it["message"].split("(")[1].split(")")[0] for it in items if it["reason"] == "panel_not_drawn"] == [
        "a chart", "a graph", "a 3D model", "a skill tree", "a quiz question"]
    assert items[2]["message"] == job_mod.DYNAMIC_MESSAGES["panel_media_missing"]
    assert set(job_mod.UNDRAWN_PANEL_KINDS) == {"model_3d", "skill_tree", "quiz"}
    # a chart or graph is warned unless a template that plots it draws the scene (these scenes are board_generic's)
    assert set(job_mod.CHART_PANEL_KINDS) == {"chart", "graph"}
    assert job_mod.dynamic_warnings([], _with_panels(["terminal", "image"])) == []  # drawn (or warned as before)
    assert job_mod.dynamic_warnings([]) == []  # without the timeline: the classic items reworded only


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
        missing("ffmpeg or Chromium missing")  # fails instead in the Docker test image
    _stored_media(asset_store, tmp_path, ffmpeg)
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    _patch_timeline(monkeypatch, lambda vid: _dynamic(vid, media_scene=1, panel="chart"))
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
    # the chart panel the Dynamic stage leaves out is reported on the render and in the job's log
    chart = job_mod.UNDRAWN_PANEL_MESSAGE.format(what="a chart")
    assert [(w["scene_id"], w["reason"], w["message"]) for w in render.options["warnings"]] == [
        ("s0", "panel_not_drawn", chart)]
    assert any(e["type"] == "log" and e["message"] == f"Scene 1 (Scene 0): {chart}" for e in ctx.events)


@pytest.mark.slow
def test_preflight_block_stops_before_capture(app_env, asset_store, monkeypatch, tmp_path: Path) -> None:
    from aadhi.compose.capabilities import chromium_installed, resolve_tool

    ffmpeg = resolve_tool(app_env.ffmpeg_path)
    if ffmpeg is None or chromium_installed() is False:
        missing("ffmpeg or Chromium missing")  # fails instead in the Docker test image
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
