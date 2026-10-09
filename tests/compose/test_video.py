"""render_video: segment specs (pure), DB state handling, and the full render (slow)."""

from __future__ import annotations

import asyncio
import re
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from aadhi.compose import video as video_mod
from aadhi.compose.ffmpeg import probe, run_ffmpeg
from aadhi.compose.screenshot import IntroShot, ScreenshotError, StateShot
from aadhi.compose.timeline import build_timeline, resolve_urls
from aadhi.compose.video import (
    BLANK_STATE,
    RenderPayload,
    SceneInputs,
    _add_asset_refs,
    branding_file,
    font_for_language,
    intro_segment_spec,
    render_video,
    scene_segment_spec,
    segment_frames,
)
from aadhi.config import ROOT_DIR, Settings
from aadhi.db import session_scope
from aadhi.jobs.base import FatalJobError, RetryableJobError, get_handler
from aadhi.models import AssetRef
from aadhi.schemas.manifest import AssetManifest, SceneAudio
from aadhi.schemas.timeline import KenBurns, MediaRef, ResolvedSidePanel
from aadhi.storage.assets import Produced
from tests.helpers import FakeJobContext

from .dbutil import create_rows, get_render
from .factories import make_manifest, make_screenplay
from .stub_server import serve_stub


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_fps=30, render_width=1280, render_height=720)


def tscene(tl, sid):
    return next(s for s in tl.scenes if s.scene_id == sid)


# --- pure helpers -------------------------------------------------------------------------------


def test_handler_registered() -> None:
    assert get_handler("render_video") is render_video


def test_payload_defaults(settings: Settings) -> None:
    p = RenderPayload.from_dict({"render_id": "4"}, settings)
    assert p == RenderPayload(4, settings.render_burn_captions, settings.render_include_intro)
    assert RenderPayload.from_dict({"render_id": 1, "burn_captions": True, "include_intro": False}, settings) == \
        RenderPayload(1, True, False)
    with pytest.raises(FatalJobError):
        RenderPayload.from_dict({}, settings)


def test_branding_file_is_safe(settings: Settings, tmp_path: Path) -> None:
    s = settings.model_copy(update={"branding_dir": tmp_path})
    (tmp_path / "ok.mp4").write_bytes(b"x")
    assert branding_file(s, "/branding/ok.mp4") == (tmp_path / "ok.mp4").resolve()
    for bad in ["/branding/../secret", "/branding/a/b.mp4", "/media/ok.mp4", "/branding/.hidden", None,
                "/branding/missing.mp4", "/branding/..\\x"]:
        assert branding_file(s, bad) is None


def test_font_for_language() -> None:
    assert font_for_language("ta-IN") == "Noto Sans Tamil" and font_for_language("hi-IN") == "Noto Sans Devanagari"
    assert font_for_language("en-IN") == "Inter" and font_for_language("") == "Inter"


@pytest.mark.parametrize("fps", [24, 25, 30, 60])
def test_segment_frames_contiguous_without_drift(settings: Settings, fps: int) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    bounds = segment_frames(tl, fps)
    assert bounds[0][0] == 0 and len(bounds) == len(tl.scenes) + 1
    for (_a0, a1), (b0, b1) in zip(bounds, bounds[1:], strict=False):
        assert a1 == b0 and b1 > b0
    assert bounds[-1][1] == round(tl.total_duration * fps)
    for (a, _b), s in zip(bounds[1:], tl.scenes, strict=True):
        assert abs(a / fps - s.start) <= 0.5 / fps + 1e-9
    no_intro = build_timeline(sp, make_manifest(sp), settings=settings, include_intro=False)
    assert len(segment_frames(no_intro, fps)) == len(no_intro.scenes)


def shots_for(scene, media_rect=None, panel_rect=None) -> list[StateShot]:
    times = [0.0] + [b.start for b in scene.beats]
    return [StateShot(t, f"k{i}", Path(f"frames/s_{i}.png"), media_rect if i == 0 else media_rect,
                      panel_rect if t >= (scene.side_panel.show_at if scene.side_panel else 0) else None, "contain")
            for i, t in enumerate(times)]


def test_scene_spec_quiz_audio(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    i = [s.scene_id for s in tl.scenes].index("s-quiz")
    q = tl.scenes[i]
    frames = segment_frames(tl, 30)[i + 1]
    inputs = SceneInputs(background=Path("C:/b/aadhi_left.mp4"), narration=Path("C:/a/narr.mp3"),
                         tick=Path("C:/a/tick.wav"), ding=Path("C:/a/ding.wav"))
    spec = scene_segment_spec(q, shots_for(q), timeline=tl, inputs=inputs, frames=frames, settings=settings)
    shift = q.start - frames[0] / 30
    assert spec.blank_state == BLANK_STATE and spec.state_fade > 0  # cross-fades on by default
    assert spec.frames == frames[1] - frames[0] and spec.width == 1280
    # RENDER_MASCOT_CONTINUITY (default): only the scene layer fades in, over the running mascot clip
    assert spec.fade_in == 0 and spec.layer_fade_in == tl.transition_seconds
    legacy = scene_segment_spec(q, shots_for(q), timeline=tl, inputs=inputs, frames=frames,
                                settings=settings.model_copy(update={"render_mascot_continuity": False}))
    assert legacy.fade_in == tl.transition_seconds and legacy.layer_fade_in == 0  # the original fade from black
    assert spec.background_kind == "video" and "-stream_loop" in spec.background.options
    paths = [inp.path for inp, _ in spec.audio]
    assert paths.count(str(inputs.tick)) == 5 and paths.count(str(inputs.ding)) == 1
    narr = next(c for inp, c in spec.audio if inp.path == str(inputs.narration))
    assert narr.delay == pytest.approx(q.audio_offset + shift)
    ticks = [c.delay for inp, c in spec.audio if inp.path == str(inputs.tick)]
    assert ticks == pytest.approx([q.quiz.countdown_start + k + shift for k in range(5)])
    ding = next(c for inp, c in spec.audio if inp.path == str(inputs.ding))
    assert ding.delay == pytest.approx(q.quiz.reveal_start + shift)
    assert [s.start for s in spec.states] == pytest.approx([0.0] + [max(0.0, b.start + shift) for b in q.beats])
    assert spec.states[1].path == "frames/s_1.png"  # POSIX paths (concat lists need them)
    hard = scene_segment_spec(q, shots_for(q), timeline=tl, inputs=inputs, frames=frames, settings=settings,
                              blank_state=None)
    assert hard.state_fade == 0 and hard.blank_state is None


def test_scene_spec_media_and_panel(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    sim = tscene(tl, "s-sim")
    frames = segment_frames(tl, 30)[sim.index + 1]
    rect = {"x": 576, "y": 160, "width": 1024, "height": 576}
    inputs = SceneInputs(background=Path("C:/b/bg.png"), background_is_image=True,
                         media={"manim-sim": Path("C:/a/manim.mp4")})
    spec = scene_segment_spec(sim, shots_for(sim, media_rect=rect), timeline=tl, inputs=inputs, frames=frames,
                              settings=settings)
    assert spec.background_kind == "image"
    (inp, layer), = spec.media
    assert layer.kind == "video" and layer.end_behavior == "freeze" and "-stream_loop" not in inp.options
    assert (layer.rect.x, layer.rect.y, layer.rect.w, layer.rect.h) == (384, 107, 682, 384)
    # no rect reported -> media is not composited; media missing locally -> skipped
    assert scene_segment_spec(sim, shots_for(sim), timeline=tl, inputs=inputs, frames=frames,
                              settings=settings).media == []
    assert scene_segment_spec(sim, shots_for(sim, media_rect=rect), timeline=tl, inputs=SceneInputs(),
                              frames=frames, settings=settings).media == []
    board = tscene(tl, "s-board")
    frames_b = segment_frames(tl, 30)[board.index + 1]
    prect = {"x": 1480, "y": 260, "width": 360, "height": 360}
    spec_b = scene_segment_spec(board, shots_for(board, panel_rect=prect), timeline=tl,
                                inputs=SceneInputs(media={"figure-aaa": Path("C:/a/fig.png")}), frames=frames_b,
                                settings=settings)
    (pinp, player), = spec_b.media
    assert player.kind == "image" and pinp.fmt == "image2"
    assert player.visible_from == pytest.approx(board.side_panel.show_at + board.start - frames_b[0] / 30)
    video_scene = tscene(tl, "s-video")
    spec_v = scene_segment_spec(video_scene, shots_for(video_scene, media_rect=rect), timeline=tl,
                                inputs=SceneInputs(media={"image-still": Path("C:/a/still.png")}),
                                frames=segment_frames(tl, 30)[video_scene.index + 1], settings=settings)
    assert spec_v.media[0][1].ken_burns is not None and spec_v.media[0][1].fit == "contain"  # page fit wins
    gif = tscene(tl, "s-noaudio")
    assert scene_segment_spec(gif, shots_for(gif, panel_rect=prect), timeline=tl, inputs=SceneInputs(),
                              frames=segment_frames(tl, 30)[gif.index + 1], settings=settings).media == []


def test_panel_video_uses_its_own_fit_and_starts_at_show_at(settings: Settings) -> None:
    """The page's media_fit belongs to the MAIN media: a contain panel clip is never cropped."""
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    video = tscene(tl, "s-video")  # ai_video: main media is "cover"
    panel_clip = MediaRef(kind="video", asset_key="manim-panel", fit="contain", end_behavior="freeze",
                          ken_burns=KenBurns())
    side = ResolvedSidePanel(panel=tscene(tl, "s-board").side_panel.panel, media=panel_clip, show_at=0.8)
    scene = video.model_copy(update={"side_panel": side})
    rect = {"x": 0, "y": 0, "width": 1920, "height": 1080}
    prect = {"x": 1480, "y": 260, "width": 360, "height": 360}
    shots = [StateShot(0.0, "k0", Path("frames/a.png"), rect, None, "cover"),
             StateShot(0.8, "k1", Path("frames/b.png"), rect, prect, "cover")]
    frames = segment_frames(tl, 30)[video.index + 1]
    spec = scene_segment_spec(scene, shots, timeline=tl, frames=frames, settings=settings,
                              inputs=SceneInputs(media={"image-still": Path("C:/a/still.png"),
                                                        "manim-panel": Path("C:/a/panel.mp4")}))
    (_, main), (pinp, panel) = spec.media
    assert main.fit == "cover"  # page-reported fit of the main media
    assert panel.fit == "contain" and panel.kind == "video" and panel.ken_burns is None
    assert panel.visible_from == pytest.approx(0.8 + video.start - frames[0] / 30)
    assert "-stream_loop" not in pinp.options  # freeze


def test_branding_dir_relative_to_another_cwd(settings: Settings, tmp_path: Path, monkeypatch) -> None:
    """A relative BRANDING_DIR (as documented in .env.example) still works with ffmpeg's cwd = work dir."""
    brand = tmp_path / "app" / "video_template"
    brand.mkdir(parents=True)
    (brand / "bgm.wav").write_bytes(_wav_bytes(0.2, 440))
    monkeypatch.chdir(tmp_path / "app")
    s = settings.model_copy(update={"branding_dir": Path("./video_template")})
    path = branding_file(s, "/branding/bgm.wav")
    assert path is not None and path.is_absolute() and path == (brand / "bgm.wav").resolve()
    work = tmp_path / "work"
    work.mkdir()
    asyncio.run(run_ffmpeg(["-i", str(path), "-f", "null", "-"], settings=s, cwd=work))  # opens from elsewhere


def test_required_asset_keys() -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None))
    video = tscene(tl, "s-video")
    keys = video_mod._scene_required_keys(video)
    assert video.media.asset_key in keys and video.audio_asset_key in keys
    if video.poster is not None and video.poster.asset_key:
        assert video.poster.asset_key not in keys  # the poster is not composited when media exists
    inter = tscene(tl, "s-interactive")
    assert "poster-1" in video_mod._scene_required_keys(inter)
    board = tscene(tl, "s-board")
    assert "figure-aaa" in video_mod._scene_required_keys(board)  # side-panel media


def test_local_copies_required_and_optional(app_env, asset_store, tmp_path: Path, monkeypatch) -> None:
    asset_store.put("narr-ok", "scene_audio", Produced(data=_wav_bytes(0.2, 300), mime="audio/wav"))
    ctx = FakeJobContext(settings=app_env, assets=asset_store)
    out = video_mod._local_copies(ctx, {"narr-ok", "tick-missing"}, tmp_path, required={"narr-ok"})
    assert set(out) == {"narr-ok"} and out["narr-ok"].is_absolute() and out["narr-ok"].exists()
    assert any(e["level"] == "warning" and "skipped" in e["message"] for e in ctx.events if e["type"] == "log")
    with pytest.raises(FatalJobError) as ei:
        video_mod._local_copies(ctx, {"narr-gone"}, tmp_path, required={"narr-gone"})
    assert ei.value.code == "asset_missing"

    def flaky(key, dest):
        raise OSError("connection reset")

    monkeypatch.setattr(asset_store, "local_copy", flaky)
    with pytest.raises(RetryableJobError):
        video_mod._local_copies(ctx, {"narr-ok"}, tmp_path, required={"narr-ok"})


@pytest.mark.parametrize(("attempt", "status"), [(1, "running"), (2, "failed")])
@pytest.mark.parametrize("error", [RetryableJobError("render page busy"), RuntimeError("boom")])
def test_retryable_failures_leave_render_restartable(app_env, asset_store, monkeypatch, attempt: int, status: str,
                                                      error: Exception) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id},
                         attempt=attempt)  # settings.job_max_attempts == 2

    async def failing(*a, **k):
        raise error

    monkeypatch.setattr(video_mod, "_render", failing)
    with pytest.raises(type(error)):
        run(ctx)
    assert get_render(rows.render_id).status == status


@pytest.mark.parametrize("retryable", [True, False])
def test_screenshot_errors_map_to_job_errors(app_env, asset_store, monkeypatch, retryable: bool) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    monkeypatch.setattr(video_mod, "_mint_token", lambda *a, **k: "tok")

    async def fail(**kwargs):
        assert kwargs["media_origins"] == [] and kwargs["check_cancelled"] == ctx.check_cancelled
        raise ScreenshotError("render page failed to boot (player_api): x", code="render_page_player_api",
                              retryable=retryable)

    monkeypatch.setattr(video_mod, "capture_render_frames", fail)
    expected = RetryableJobError if retryable else FatalJobError
    with pytest.raises(expected) as ei:
        run(ctx)
    if not retryable:
        assert ei.value.code == "render_page_player_api"
    assert get_render(rows.render_id).status == ("running" if retryable else "failed")


def test_intro_spec(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, None, settings=settings)
    frames = segment_frames(tl, 30)[0][1]
    shots = [IntroShot(c.start + 1, Path(f"frames/intro_{n}.png")) for n, c in enumerate(tl.intro.cards)]
    spec = intro_segment_spec(tl, shots, frames=frames, settings=settings, logo=Path("C:/b/logo.mp4"),
                              logo_has_audio=True, background=Path("C:/b/bg.png"))
    assert spec.frames == 345 and spec.background_kind == "image" and spec.blank_state == BLANK_STATE
    (_, logo), = spec.media
    assert logo.visible_to == pytest.approx(5.5) and logo.rect.w == 1280 and logo.end_behavior == "freeze"
    (_, clip), = spec.audio
    assert clip.duration == pytest.approx(5.5) and clip.fade_out > 0
    # each card from its start; the transparent blank (logo underneath) is shown before the first one
    assert [s.path for s in spec.states] == ["frames/intro_0.png", "frames/intro_1.png"]
    assert [s.start for s in spec.states] == pytest.approx([5.5, 8.5])
    silent = intro_segment_spec(tl, shots, frames=frames, settings=settings, logo=None, logo_has_audio=False,
                                background=None)
    assert silent.media == [] and silent.audio == [] and silent.background_kind == "color"


# --- DB paths (no rendering) --------------------------------------------------------------------


def run(ctx) -> dict:
    return asyncio.run(render_video(ctx))


def test_missing_timeline_fails_and_marks_render(app_env, asset_store) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp), with_timeline=False)
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    with pytest.raises(FatalJobError) as ei:
        run(ctx)
    assert ei.value.code == "timeline_missing"
    assert get_render(rows.render_id).status == "failed"


def test_stale_timeline_fails(app_env, asset_store) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp), revision=4, built_revision=3)
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    with pytest.raises(FatalJobError) as ei:
        run(ctx)
    assert ei.value.code == "timeline_stale"


def test_unknown_render_and_wrong_version(app_env, asset_store) -> None:
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": 999})
    with pytest.raises(FatalJobError, match="not found"):
        run(ctx)
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx2 = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id},
                          version_id=rows.version_id + 100)
    with pytest.raises(FatalJobError):
        run(ctx2)


def test_succeeded_render_is_idempotent(app_env, asset_store) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp), render_status="succeeded")
    with session_scope() as db:
        from aadhi.models import Render

        db.get(Render, rows.render_id).video_asset_key = "render-x"
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    assert run(ctx)["video_asset_key"] == "render-x"


def test_cancelled_render_cannot_start(app_env, asset_store) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp), render_status="cancelled")
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})
    with pytest.raises(FatalJobError, match="cannot start"):
        run(ctx)


def test_add_asset_refs_idempotent(app_env, asset_store) -> None:
    sp = make_screenplay()
    rows = create_rows(app_env, sp, None)
    for _ in range(2):
        with session_scope() as db:
            _add_asset_refs(db, rows.project_id, ["a", "b", "a", ""])
    with session_scope() as db:
        keys = sorted(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == rows.project_id)).scalars())
    assert keys == ["a", "b"]


# --- full render (slow) -------------------------------------------------------------------------


def _wav_bytes(seconds: float, freq: float, rate: int = 24000) -> bytes:
    import io

    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _store_real_assets(asset_store, manifest: AssetManifest, tmp: Path, settings: Settings) -> AssetManifest:
    """Replace the factory's fake asset keys with real stored files (audio, manim clip, images)."""
    audio: dict[str, SceneAudio] = {}
    for n, (sid, sa) in enumerate(manifest.audio.items()):
        a = asset_store.put(sa.asset_key, "scene_audio", Produced(data=_wav_bytes(sa.duration, 300 + 40 * n),
                                                                  mime="audio/wav", duration_s=sa.duration))
        audio[sid] = sa.model_copy(update={"storage_key": a.storage_key, "mime": "audio/wav"})
    clip = tmp / "manim.mp4"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=3", "-c:v", "libx264", "-pix_fmt",
                            "yuv420p", "-preset", "ultrafast", str(clip)], settings=settings))
    asset_store.put("manim-sim", "manim", Produced(path=clip, mime="video/mp4", duration_s=3.0, width=640, height=360))
    asset_store.put("image-still", "image", Produced(data=_png_bytes((1024, 576), (30, 120, 200)), mime="image/png"))
    asset_store.put("poster-1", "poster", Produced(data=_png_bytes((800, 450), (200, 80, 30)), mime="image/png"))
    asset_store.put("figure-aaa", "figure", Produced(data=_png_bytes((640, 480), (240, 240, 240)), mime="image/png"))
    return manifest.model_copy(update={"audio": audio})


@pytest.mark.slow
@pytest.mark.parametrize("burn", [True])
def test_full_render(app_env, asset_store, tmp_path: Path, monkeypatch, burn: bool) -> None:
    sp = make_screenplay()
    base_settings = app_env.model_copy(update={"render_width": 640, "render_height": 360, "render_fps": 15,
                                               "render_preset": "ultrafast", "render_crf": 30,
                                               "branding_dir": ROOT_DIR / "video_template"})
    manifest = _store_real_assets(asset_store, make_manifest(sp), tmp_path, base_settings)
    rows = create_rows(base_settings, sp, manifest)
    tl = build_timeline(sp, manifest, settings=base_settings, version_id=rows.version_id, revision=3)
    minted: list[tuple] = []

    def fake_token(settings, version_id, ttl, *, render_id=None, include_intro=None):
        minted.append((version_id, ttl, render_id, include_intro))
        return "render-token-1"

    monkeypatch.setattr(video_mod, "_mint_token", fake_token)
    with serve_stub("render-token-1", resolve_urls(tl, asset_store).model_dump(mode="json")) as (base, state):
        settings = base_settings.model_copy(update={"base_url": base})
        ctx = FakeJobContext(settings=settings, assets=asset_store, version_id=rows.version_id,
                             project_id=rows.project_id,
                             payload={"render_id": rows.render_id, "burn_captions": burn, "include_intro": True,
                                      "soft_subtitles": True})
        result = asyncio.run(render_video(ctx))

    assert minted == [(rows.version_id, video_mod.RENDER_TOKEN_TTL_SECONDS, rows.render_id, True)]
    render = get_render(rows.render_id)
    assert render.status == "succeeded"
    assert render.video_asset_key == result["video_asset_key"]
    assert render.built_revision == 3
    assert render.chapters_text.startswith("00:00 Introduction")
    assert render.duration_s == pytest.approx(tl.total_duration, abs=0.3)

    video_asset = asset_store.get(result["video_asset_key"])
    assert video_asset.kind == "render" and video_asset.mime == "video/mp4"
    path = asset_store.storage.local_path(video_asset.storage_key)
    info = asyncio.run(probe(path, settings=settings))
    assert info.duration == pytest.approx(tl.total_duration, abs=0.3)
    assert (info.width, info.height) == (640, 360) and info.video_codec == "h264" and info.audio_codec == "aac"
    assert len(info.chapters) == len(tl.chapters) >= 2
    assert info.chapters[1]["title"] == tl.chapters[1].title
    with open(path, "rb") as f:
        head = f.read(4096)
    assert head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))

    srt = asset_store.storage.get_bytes(asset_store.get(result["srt_asset_key"]).storage_key).decode()
    vtt = asset_store.storage.get_bytes(asset_store.get(result["vtt_asset_key"]).storage_key).decode()
    assert srt.startswith("1\n") and re.search(r"\d\d:\d\d:\d\d,\d{3} --> ", srt)
    assert vtt.startswith("WEBVTT")
    assert asset_store.get(result["srt_asset_key"]).kind == "captions"
    with session_scope() as db:
        refs = set(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == rows.project_id)).scalars())
    assert {result["video_asset_key"], result["srt_asset_key"], result["vtt_asset_key"]} <= refs
    stages = [e["stage"] for e in ctx.events if e["type"] == "progress"]
    assert "screenshots" in stages and "segments" in stages and stages[-1] == "done"
    progress = [e["progress"] for e in ctx.events if e["type"] == "progress"]
    assert progress == sorted(progress)

    # spot-check composed frames (output is 1/3 of the 1920x1080 stage)
    def frame_at(t: float, name: str) -> np.ndarray:
        out = tmp_path / name
        asyncio.run(run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", str(out)], settings=settings))
        with Image.open(out) as im:
            return np.asarray(im.convert("RGB")).astype(int)

    quiz = tscene(tl, "s-quiz")
    px = frame_at(quiz.start + quiz.quiz.countdown_start + 0.5, "quiz.png")
    box = px[75:115, 455:485]  # right part of the quiz box (no text): rgba(40,15,60,0.9) over the background
    assert box[..., 0].mean() < 100 and box[..., 2].mean() > box[..., 0].mean() > box[..., 1].mean()
    board = tscene(tl, "s-board")
    px = frame_at(board.start + board.side_panel.show_at + 0.5, "panel.png")
    assert np.abs(px[140:155, 545:560] - 240).max() < 25  # figure (240 grey) shows through the panel hole
    sim = tscene(tl, "s-sim")
    px = frame_at(sim.start + 1.5, "sim.png")
    assert px[60:240, 200:525].std() > 40  # manim clip (test pattern) inside the media rect

    # audio placement: narration loud inside its beats, only (ducked) BGM in the silent chapter card
    wav = tmp_path / "final.wav"
    asyncio.run(run_ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(wav)],
                           settings=settings))
    with wave.open(str(wav)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)

    def rms(a: float, b: float) -> float:
        return float(np.sqrt(np.mean(pcm[int(a * 8000): int(b * 8000)] ** 2)))

    title, chapter = tscene(tl, "s-title"), tscene(tl, "s-chapter")
    beat = title.beats[0]
    speech = rms(title.start + beat.start + 0.1, title.start + beat.speech_end - 0.1)
    silent = rms(chapter.start + 0.5, chapter.start + chapter.duration - 0.5)
    assert speech > 4 * max(silent, 1.0)
    assert rms(title.start + 0.05, title.start + beat.start - 0.1) < speech / 4  # lead-in before narration

    # --- output QA recorded on the render, and the video QA helper's frame-accurate checks ---------
    from .test_render_parity_slow import vqa

    qa = render.options["qa"]
    assert qa["ok"] is True and qa["frames"] == qa["expected_frames"] == round(tl.total_duration * 15)
    assert qa["constant_frame_rate"] and qa["audible"] and qa["black_edges"] == [] and qa["problems"] == []
    reasons = {(w["scene_id"], w["reason"]) for w in render.options["warnings"]}
    assert {("s-noaudio", "no_audio"), ("s-sim-fallback", "simulation_without_media"),
            ("s-noaudio", "panel_not_in_video")} <= reasons
    assert render.options["burn_captions"] is False  # the row's own options (dbutil) are kept: merged, not replaced
    cfr = vqa.check_cfr(path, 15)
    assert cfr["ok"], cfr
    info2 = vqa.probe(path)
    assert info2["color"]["color_space"] == "bt709" and info2["color"]["color_range"] == "tv"
    assert info2["subtitles"] == [] if burn else len(info2["subtitles"]) == 1  # no double captions with burn-in
    assert vqa.black_edges(path, [tl.total_duration * f for f in (0.3, 0.6, 0.9)]) == []
    assert [c["title"] for c in info2["chapters"]] == [c.title for c in tl.chapters]
    assert [round(c["start"], 2) for c in info2["chapters"]] == [round(c.start, 2) for c in tl.chapters]
    cues = vqa.parse_vtt(vtt)
    assert [round(c[0], 3) for c in cues] == [round(c.start, 3) for c in tl.captions]
    onset = vqa.audio_onset(vqa.audio_pcm(path), 8000, title.start + beat.start - 0.3, threshold=2000)
    assert onset is not None and abs(onset - (title.start + beat.start)) <= 0.1  # narration starts on its beat


def test_scene_spec_interactive_poster_and_estimated_scene(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    inter = tscene(tl, "s-interactive")
    rect = {"x": 576, "y": 160, "width": 1024, "height": 576}
    spec = scene_segment_spec(inter, shots_for(inter, media_rect=rect), timeline=tl,
                              inputs=SceneInputs(media={"poster-1": Path("C:/a/poster.png")},
                                                 narration=Path("C:/a/n.mp3")),
                              frames=segment_frames(tl, 30)[inter.index + 1], settings=settings)
    (inp, layer), = spec.media
    assert layer.kind == "image" and inp.fmt == "image2" and layer.ken_burns is None
    assert len(spec.audio) == 1
    est = tscene(tl, "s-noaudio")  # no narration file: silent segment, states only
    spec2 = scene_segment_spec(est, shots_for(est), timeline=tl, inputs=SceneInputs(narration=Path("C:/a/x.mp3")),
                               frames=segment_frames(tl, 30)[est.index + 1], settings=settings)
    assert spec2.audio == [] and spec2.states and spec2.background_kind == "color"


def test_user_cancel_marks_render_cancelled(app_env, asset_store) -> None:
    from aadhi.jobs.base import JobCancelled

    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id}, cancelled=True)
    with pytest.raises(JobCancelled):
        run(ctx)
    assert get_render(rows.render_id).status == "cancelled"


def test_lease_loss_leaves_render_restartable(app_env, asset_store, monkeypatch) -> None:
    from aadhi.jobs.base import JobCancelled

    sp = make_screenplay()
    rows = create_rows(app_env, sp, make_manifest(sp))
    ctx = FakeJobContext(settings=app_env, assets=asset_store, payload={"render_id": rows.render_id})

    def lost() -> None:
        raise JobCancelled("lease lost", reason="lease_lost")

    monkeypatch.setattr(ctx, "check_cancelled", lost)
    with pytest.raises(JobCancelled):
        run(ctx)
    assert get_render(rows.render_id).status == "running"
