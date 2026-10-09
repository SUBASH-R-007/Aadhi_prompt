"""Real ffmpeg encodes for the render-parity work: frame grid after the concat copy, BT.709 colour,
the soft caption track, the mascot loop continuing across scenes and its crossfade, and a retried
render reusing its encoded segments."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from aadhi.compose import video as video_mod
from aadhi.compose.captions import to_srt
from aadhi.compose.chapters import to_ffmetadata
from aadhi.compose.ffmpeg import (
    AudioClip,
    FinalSpec,
    Input,
    MediaLayer,
    Rect,
    SegmentSpec,
    StateFrame,
    build_final,
    build_segment,
    concat_list,
    image_input,
    loop_phase_frames,
    run_ffmpeg,
    state_mixes,
    video_input,
)
from aadhi.compose.frames import transparent_png, write_state_mixes
from aadhi.compose.timeline import build_timeline, resolve_urls
from aadhi.compose.workspace import work_root
from aadhi.config import ROOT_DIR, Settings
from aadhi.schemas.timeline import CaptionCue, Chapter
from tests.helpers import FakeJobContext

from .dbutil import create_rows, get_render
from .factories import make_manifest, make_screenplay
from .stub_server import serve_stub

pytestmark = pytest.mark.slow

FPS = 30
W, H = 640, 360
CLIP = ROOT_DIR / "video_template" / "aadhi_left.mp4"
OTHER_CLIP = ROOT_DIR / "video_template" / "aadhi_right.mp4"


def load_video_qa():
    spec = importlib.util.spec_from_file_location("aadhi_eval_video_qa", ROOT_DIR / "evals" / "video_qa.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vqa = load_video_qa()


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_preset="ultrafast", render_crf=20)


def encode(spec: SegmentSpec, work: Path, name: str, settings: Settings) -> Path:
    cmd = build_segment(spec, name=name)
    for k, v in cmd.files.items():
        (work / k).write_text(v, encoding="utf-8")
    write_state_mixes(state_mixes(spec), work)  # the cross-fade frames (the renderer writes them too)
    asyncio.run(run_ffmpeg(cmd.args, settings=settings, cwd=work, timeout=600))
    return work / f"{name}.mp4"


def mux(work: Path, names: list[str], settings: Settings, total_frames: int, **extra) -> Path:
    total = total_frames / FPS
    (work / "v.ffconcat").write_text(concat_list([f"{n}.mp4" for n in names]), encoding="utf-8")
    (work / "a.ffconcat").write_text(concat_list([f"{n}.wav" for n in names]), encoding="utf-8")
    (work / "m.ffmeta").write_text(to_ffmetadata([Chapter(start=0, title="A"), Chapter(start=1, title="B")], total),
                                   encoding="utf-8")
    spec = FinalSpec(video_list="v.ffconcat", audio_list="a.ffconcat", metadata="m.ffmeta", total_seconds=total,
                     output="final.mp4", width=W, height=H, fps=FPS, **extra)
    cmd = build_final(spec, filter_script="f.fg")
    for k, v in cmd.files.items():
        (work / k).write_text(v, encoding="utf-8")
    asyncio.run(run_ffmpeg(cmd.args, settings=settings, cwd=work, timeout=300))
    return work / "final.mp4"


def board_png(path: Path, color=(240, 200, 40)) -> Path:
    """A 'board' on the right half: the mascot region (left) stays visible."""
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((1000, 150, 1880, 900), fill=(*color, 255))
    im.save(path)
    return path


# --- frame grid -----------------------------------------------------------------------------------


@pytest.mark.parametrize("bt709", [False, True])
def test_concat_keeps_every_frame_on_the_grid(tmp_path: Path, bt709: bool) -> None:
    """B-frames (preset medium) and odd segment lengths: frame n is at exactly n/30 s after the copy."""
    settings = Settings(_env_file=None)
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    board_png(tmp_path / "b.png")
    names, total = [], 0
    for n, frames in enumerate((37, 53, 41)):
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=frames, crf=28, preset="medium",
                           blank_state="blank.png", bt709=bt709, layer_fade_in=0.4,
                           background=video_input(CLIP, loop=True, audio=False), background_kind="video",
                           background_offset_frames=loop_phase_frames(total, fps=FPS, clip_seconds=8.0))
        spec.states = [StateFrame("b.png", 0.0)]
        encode(spec, tmp_path, f"seg{n}", settings)
        names.append(f"seg{n}")
        total += frames
    final = mux(tmp_path, names, settings, total, bt709=bt709)
    report = vqa.check_cfr(final, FPS)
    assert report["ok"], report
    assert report["frames"] == total == 131 and report["step"] == 512
    info = vqa.probe(final)
    assert info["r_frame_rate"] == info["avg_frame_rate"] == "30/1"
    assert (info["color"]["color_space"] == "bt709") is bt709


# --- colour -----------------------------------------------------------------------------------------


def _decode(path: Path, index: int, *, as_709: bool = False) -> np.ndarray:
    vf = f"select=eq(n\\,{index})" + (",scale=in_color_matrix=bt709:in_range=tv" if as_709 else "")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


def test_bt709_colours_match_the_screenshot_and_still_pixels(tmp_path: Path, settings: Settings) -> None:
    board, still = (200, 80, 30), (30, 160, 220)
    im = Image.new("RGBA", (1920, 1080), (*board, 255))
    ImageDraw.Draw(im).rectangle((60, 60, 660, 510), fill=(0, 0, 0, 0))  # a hole for the media
    im.save(tmp_path / "state.png")
    Image.new("RGB", (400, 300), still).save(tmp_path / "still.png")
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    out = {}
    for bt709 in (False, True):
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=15, crf=4, preset="ultrafast", bt709=bt709,
                           blank_state="blank.png")
        spec.states = [StateFrame("state.png", 0.0)]
        spec.media.append((image_input(tmp_path / "still.png", FPS),
                           MediaLayer(0, "image", Rect(20, 20, 200, 150), fit="cover")))
        out[bt709] = encode(spec, tmp_path, f"c{int(bt709)}", settings)
    tagged = _decode(out[True], 7)  # ffmpeg honours the BT.709 tags, as browsers and players do
    assert np.abs(tagged[300, 500] - board).max() <= 3 and np.abs(tagged[90, 120] - still).max() <= 3
    legacy_in_browser = _decode(out[False], 7, as_709=True)  # untagged HD video is shown as BT.709
    assert np.abs(legacy_in_browser[300, 500] - board).max() > 6  # the drift the tags fix
    assert vqa.probe(out[True])["color"] == {"color_space": "bt709", "color_primaries": "bt709",
                                             "color_transfer": "bt709", "color_range": "tv"}


# --- soft caption track ---------------------------------------------------------------------------


def test_soft_caption_track_is_selectable(tmp_path: Path, settings: Settings) -> None:
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    tone = tmp_path / "tone.wav"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=500:duration=1", str(tone)], settings=settings))
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=60, crf=28, preset="ultrafast")
    spec.audio.append((Input(str(tone)), AudioClip(0, delay=0.2)))
    encode(spec, tmp_path, "seg", settings)
    cues = [CaptionCue(start=0.2, end=1.0, text="வணக்கம் மாணவர்களே"), CaptionCue(start=1.1, end=1.9, text="V = I R")]
    (tmp_path / "captions.soft.srt").write_text(to_srt(cues), encoding="utf-8")
    final = mux(tmp_path, ["seg"], settings, 60, soft_subtitles="captions.soft.srt", subtitle_language="tam")
    info = vqa.probe(final)
    (track,) = info["subtitles"]
    # requested non-default; the MP4 muxer still enables a file's only caption track (tkhd), see build_final
    assert track["codec"] == "mov_text" and track["language"] == "tam"
    assert info["audio"]["codec_name"] == "aac" and info["codec"] == "h264" and len(info["chapters"]) == 2
    srt = tmp_path / "back.srt"
    asyncio.run(run_ffmpeg(["-i", str(final), "-map", "0:s:0", str(srt)], settings=settings))
    back = vqa.parse_srt(srt.read_text(encoding="utf-8"))
    assert [c[2] for c in back] == ["வணக்கம் மாணவர்களே", "V = I R"]
    assert back[0][0] == pytest.approx(0.2, abs=0.01)


# --- mascot continuity ----------------------------------------------------------------------------


def _mascot(frame: np.ndarray) -> np.ndarray:
    return frame[40:330, 0:236].astype(float)  # left mascot region (x <= 708 of 1920)


def _two_scenes(tmp_path: Path, settings: Settings, *, continuity: bool) -> tuple[Path, int]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    board_png(tmp_path / "b0.png", (240, 200, 40))
    board_png(tmp_path / "b1.png", (40, 200, 240))
    names, start = [], 0
    for n, frames in enumerate((50, 40)):
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=frames, crf=18, preset="ultrafast",
                           blank_state="blank.png", background=video_input(CLIP, loop=True, audio=False),
                           background_kind="video")
        if continuity:
            spec.layer_fade_in = 0.4
            spec.background_offset_frames = loop_phase_frames(start, fps=FPS, clip_seconds=8.0)
        else:
            spec.fade_in = 0.4
        spec.states = [StateFrame(f"b{n}.png", 0.0)]
        encode(spec, tmp_path, f"s{n}", settings)
        names.append(f"s{n}")
        start += frames
    return mux(tmp_path, names, settings, start), 50


def test_mascot_keeps_moving_across_scenes_and_only_the_scene_layer_fades(tmp_path: Path,
                                                                           settings: Settings) -> None:
    final, seam = _two_scenes(tmp_path / "new", settings, continuity=True)
    frames = [vqa.frame_rgb(final, i, FPS) for i in range(seam - 4, seam + 4)]
    inner = [float(np.abs(_mascot(a) - _mascot(b)).mean()) for a, b in zip(frames[:3], frames[1:4], strict=True)]
    jump = float(np.abs(_mascot(frames[3]) - _mascot(frames[4])).mean())
    assert jump <= max(inner) * 3 + 2, (jump, inner)  # the loop continues: no pose jump at the seam
    assert _mascot(frames[4]).mean() > 0.85 * _mascot(frames[3]).mean()  # no dip to black
    board_at_seam = frames[4][150:250, 400:560].astype(float).mean()
    board_later = vqa.frame_rgb(final, seam + 20, FPS)[150:250, 400:560].astype(float).mean()
    assert abs(board_at_seam - board_later) > 20  # the new scene's board is still fading in

    legacy, _ = _two_scenes(tmp_path / "old", settings, continuity=False)
    old = [vqa.frame_rgb(legacy, i, FPS) for i in (seam - 1, seam)]
    assert _mascot(old[1]).mean() < 0.3 * _mascot(old[0]).mean()  # the original dip to black


def test_mascot_crossfades_on_a_position_change(tmp_path: Path, settings: Settings) -> None:
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    phase = loop_phase_frames(500, fps=FPS, clip_seconds=8.0)

    def segment(name: str, bg: Path, prev: Path | None) -> Path:
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=30, crf=12, preset="ultrafast", blank_state="blank.png",
                           background=video_input(bg, loop=True, audio=False), background_kind="video",
                           background_offset_frames=phase)
        if prev is not None:
            spec.previous_background = video_input(prev, loop=True, audio=False)
            spec.previous_background_offset_frames = phase
        return encode(spec, tmp_path, name, settings)

    xf = segment("xf", OTHER_CLIP, CLIP)
    only_old, only_new = segment("old", CLIP, None), segment("new", OTHER_CLIP, None)

    def diff(a: Path, b: Path, i: int) -> float:
        return float(np.abs(vqa.frame_rgb(a, i, FPS).astype(float) - vqa.frame_rgb(b, i, FPS).astype(float)).mean())

    assert diff(xf, only_old, 0) < 3  # starts on the previous clip, continuing its loop
    assert diff(xf, only_new, 25) < 3  # 0.6 s later only the new clip remains
    assert diff(xf, only_old, 9) > 3 and diff(xf, only_new, 9) > 3  # mid-crossfade: a blend


# --- resumable render -----------------------------------------------------------------------------


def _wav_bytes(seconds: float, freq: float, rate: int = 24000) -> bytes:
    import io
    import wave

    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def test_a_retried_render_reuses_its_encoded_segments(app_env, asset_store, monkeypatch) -> None:
    from aadhi.schemas.manifest import AssetManifest
    from aadhi.storage.assets import Produced

    data = make_screenplay().model_dump(mode="json")
    data["scenes"] = [s for s in data["scenes"] if s["id"] in ("s-title", "s-chapter", "s-quiz")]
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    manifest = make_manifest(sp)
    audio = {}
    for n, (sid, sa) in enumerate(manifest.audio.items()):
        a = asset_store.put(sa.asset_key, "scene_audio", Produced(data=_wav_bytes(sa.duration, 300 + 50 * n),
                                                                  mime="audio/wav", duration_s=sa.duration))
        audio[sid] = sa.model_copy(update={"storage_key": a.storage_key, "mime": "audio/wav"})
    manifest = AssetManifest(audio=audio, media={})
    settings = app_env.model_copy(update={"render_width": 640, "render_height": 360, "render_fps": 15,
                                          "render_preset": "ultrafast", "render_crf": 30,
                                          "branding_dir": ROOT_DIR / "video_template"})
    rows = create_rows(settings, sp, manifest)
    tl = build_timeline(sp, manifest, settings=settings, version_id=rows.version_id, revision=3)
    monkeypatch.setattr(video_mod, "_mint_token", lambda *a, **k: "tok-resume")
    encodes: list[str] = []
    real_run = video_mod._run

    async def spy(cmd, ctx, work, *, timeout):
        out = next((a for a in cmd.args if a.endswith(".wav")), "")
        encodes.append(out or "final")
        await real_run(cmd, ctx, work, timeout=timeout)

    monkeypatch.setattr(video_mod, "_run", spy)
    real_final = video_mod.build_final
    with serve_stub("tok-resume", resolve_urls(tl, asset_store).model_dump(mode="json")) as (base, _):
        s = settings.model_copy(update={"base_url": base})

        def broken_final(*a, **k):
            raise RuntimeError("mux crashed")

        monkeypatch.setattr(video_mod, "build_final", broken_final)
        ctx1 = FakeJobContext(settings=s, assets=asset_store, version_id=rows.version_id, project_id=rows.project_id,
                              payload={"render_id": rows.render_id}, attempt=1)
        with pytest.raises(RuntimeError, match="mux crashed"):
            asyncio.run(video_mod.render_video(ctx1))
        assert get_render(rows.render_id).status == "running"  # retryable: left restartable
        first = sorted(encodes)
        assert len(first) == len(tl.scenes) + 1  # intro + every scene encoded
        ws_root = work_root(s) / f"render-{rows.render_id}"
        assert len(list((ws_root / "segments").glob("*.ok"))) == len(first)
        assert not [p for p in ws_root.iterdir() if p.name.startswith("attempt-")]

        encodes.clear()
        monkeypatch.setattr(video_mod, "build_final", real_final)
        ctx2 = FakeJobContext(settings=s, assets=asset_store, version_id=rows.version_id, project_id=rows.project_id,
                              payload={"render_id": rows.render_id}, attempt=2)
        result = asyncio.run(video_mod.render_video(ctx2))
    assert encodes == ["final"]  # every segment came from the workspace
    assert any("Reused" in e["message"] for e in ctx2.events if e["type"] == "log")
    render = get_render(rows.render_id)
    assert render.status == "succeeded" and result["video_asset_key"] == render.video_asset_key
    assert render.options["qa"]["ok"] is True
    assert not ws_root.exists()  # removed after success
    path = asset_store.storage.local_path(asset_store.get(result["video_asset_key"]).storage_key)
    assert vqa.check_cfr(path, 15)["ok"]


def test_parity_options_keep_memory_bounded_with_many_states(tmp_path: Path, settings: Settings) -> None:
    """120 distinct 1080p states with BT.709 conversion, the scene-layer fade and a mascot crossfade."""
    import time as _time

    import psutil

    from aadhi.compose.ffmpeg import ffmpeg_base, subprocess_env

    n = 120
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(n):
        im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((100 + i, 100, 900 + i, 900), fill=((i * 37) % 256, (i * 91) % 256, 200, 255))
        im.save(frames_dir / f"s{i:04d}.png")
    transparent_png(frames_dir / "blank.png", (1920, 1080))
    spec = SegmentSpec(width=1920, height=1080, fps=FPS, frames=600, crf=35, preset="ultrafast",
                       blank_state="frames/blank.png", bt709=True, layer_fade_in=0.4,
                       background=video_input(CLIP, loop=True, audio=False), background_kind="video",
                       background_offset_frames=100, previous_background=video_input(OTHER_CLIP, loop=True,
                                                                                     audio=False))
    spec.states = [StateFrame(f"frames/s{i:04d}.png", i * 20.0 / n) for i in range(n)]
    cmd = build_segment(spec, name="many")
    for k, v in cmd.files.items():
        (tmp_path / k).write_text(v, encoding="utf-8")
    write_state_mixes(state_mixes(spec), tmp_path)
    argv = [*ffmpeg_base(settings), *cmd.args]
    proc = subprocess.Popen(argv, cwd=tmp_path, env=subprocess_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    peak, ps, t0 = 0, psutil.Process(proc.pid), _time.monotonic()
    while proc.poll() is None:
        try:
            peak = max(peak, ps.memory_info().rss)
        except psutil.Error:
            break
        if _time.monotonic() - t0 > 600:
            proc.kill()
            pytest.fail("encode took too long")
        _time.sleep(0.05)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    assert proc.returncode == 0, err[-500:]
    assert peak < 900 * 1024 * 1024, f"peak RSS {peak / 2**20:.0f} MB"
    assert vqa.probe(tmp_path / "many.mp4")["frames"] == 600


# --- untagged media videos and translucent cross-fades (regressions of the parity work) -------------------


def _all_frames(path: Path, w: int = W, h: int = H, *, as_709: bool = True) -> np.ndarray:
    vf = ["-vf", "scale=in_color_matrix=bt709:in_range=tv"] if as_709 else []
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), *vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(int)


def test_untagged_hd_media_keeps_the_colours_the_browser_shows(tmp_path: Path, settings: Settings) -> None:
    from aadhi.compose.ffmpeg import probe, untagged_hd

    yuv = bytes([81]) * (1280 * 720) + bytes([107]) * (640 * 360) + bytes([192]) * (640 * 360)
    (tmp_path / "frame.yuv").write_bytes(yuv * 10)
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    for name, extra in (("untagged.mp4", []), ("tagged.mp4", tags)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "1280x720",
                        "-r", str(FPS), "-i", str(tmp_path / "frame.yuv"), "-c:v", "libx264", "-qp", "0",
                        "-pix_fmt", "yuv420p", *extra, str(tmp_path / name)], check=True, timeout=120)
    for name in ("untagged.mp4", "tagged.mp4"):
        # what the browser shows: the source decoded as BT.709 limited range (its tag, or the untagged-HD rule)
        want = _all_frames(tmp_path / name, 1280, 720)[3, 360, 640]
        info = asyncio.run(probe(tmp_path / name, settings=settings))
        assert untagged_hd(info) is (name == "untagged.mp4")
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=8, crf=4, preset="ultrafast", bt709=True)
        spec.media.append((video_input(tmp_path / name, audio=False),
                           MediaLayer(0, "video", Rect(0, 0, W, H), fit="cover", untagged_yuv=untagged_hd(info),
                                      range_tagged=bool(info.color_range))))
        got = _all_frames(encode(spec, tmp_path, name.split(".")[0], settings))[4, H // 2, W // 2]
        assert np.abs(got - want).max() <= 3, (name, got, want)


def test_a_translucent_glass_panel_keeps_its_opacity_through_a_state_change(tmp_path: Path,
                                                                            settings: Settings) -> None:
    glass = (55, 15, 100, round(0.86 * 255))
    Image.new("RGB", (640, 360), (200, 200, 200)).save(tmp_path / "bg.png")
    old = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(old).rectangle((900, 120, 1860, 960), fill=glass)
    new = old.copy()
    ImageDraw.Draw(new).rectangle((960, 180, 1500, 260), fill=(250, 250, 250, 255))  # a bullet is revealed
    ImageDraw.Draw(new).rectangle((90, 720, 570, 1020), fill=(10, 200, 30, glass[3]))  # glass over empty space
    old.save(tmp_path / "a.png")
    new.save(tmp_path / "b.png")
    transparent_png(tmp_path / "blank.png", (1920, 1080))
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=30, crf=1, preset="ultrafast", bt709=True,
                       blank_state="blank.png", background=image_input(tmp_path / "bg.png", FPS),
                       background_kind="image")
    spec.states = [StateFrame("a.png", 0.0), StateFrame("b.png", 0.5)]
    frames = _all_frames(encode(spec, tmp_path, "glass", settings))
    board = frames[:, 250, 520]  # a text-free board pixel (stage 1560, 750)
    assert np.abs(board - board[0]).max() <= 2, board.tolist()  # never darker during the cross-fade
    chip = frames[:, 290, 110].sum(axis=1)  # the new glass chip over the light background
    start, end = chip[0], chip[-1]
    assert end < start - 100  # it does appear
    ramp = chip[15:20]
    assert all(b <= a + 3 for a, b in zip(chip[:-1], chip[1:], strict=True))  # moves one way only
    assert ramp.min() >= end - 3 and ramp.max() <= start + 3  # never darker (or lighter) than either end
