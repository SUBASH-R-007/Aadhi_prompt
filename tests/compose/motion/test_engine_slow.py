"""The motion engine end to end against the stub stage (real Chromium headless shell, real ffmpeg; slow).

The stub draws each frame's number as 16 blocks and the extracted media frame's own number below it, so decoding the
MP4 proves: every frame exactly once and in order across chunk joins (no dropped, duplicated or reordered frame),
media frames picked and served correctly, captions layered by the stage, chunks reused by a retry, page recycling
and the contract errors. The real stage (``web/render-dynamic.html``) is exercised by the stage area's tests and by
``scripts/bench_motion.py``. The two ``container`` tests also run in the Docker ``test`` image (``pytest -m container``).
"""

from __future__ import annotations

import asyncio
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from aadhi.compose.capabilities import chromium_installed, resolve_tool
from aadhi.compose.dynamic.stage_page import StageError
from aadhi.compose.ffmpeg import FFmpegError
from aadhi.compose.motion.cache import ChunkCache
from aadhi.compose.motion.engine import MotionConfig, MotionError, render_motion
from aadhi.config import Settings
from aadhi.jobs.base import JobCancelled

from .fixtures import (
    AUDIO_KEY,
    CLIP_KEY,
    TOKEN,
    SimpleCtx,
    decode_bits,
    dynamic_timeline,
    gray_frames,
    make_bits_video,
    make_wav,
)
from .stage_server import serve_stage

pytestmark = pytest.mark.slow

CLIP_FRAMES = 60


@pytest.fixture(scope="module")
def ffmpeg() -> str:
    path = resolve_tool(Settings(_env_file=None).ffmpeg_path)
    if path is None or resolve_tool(Settings(_env_file=None).ffprobe_path) is None:
        pytest.skip("ffmpeg/ffprobe are not installed")
    if chromium_installed() is False:
        pytest.skip("Playwright's Chromium is not installed")
    return path


@pytest.fixture(scope="module")
def media(ffmpeg: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("motion-media")
    return {AUDIO_KEY: make_wav(root / "narration.wav", 1.0),
            CLIP_KEY: make_bits_video(ffmpeg, root / "clip.mp4", CLIP_FRAMES)}


def _settings(base: str, tmp: Path, **kw) -> Settings:
    # web_dir: the chunk keys hash the stage bundle under it; a fixed stand-in keeps them stable while web/ changes
    web = tmp.parent / "web"
    (web / "js").mkdir(parents=True, exist_ok=True)
    return Settings(_env_file=None, base_url=base, render_bgm=False, render_slot_dir=str(tmp / "slots"),
                    render_cpu_slots=8, web_dir=web, **kw)


def _render(tl, tmp: Path, media: dict[str, Path], *, config: dict | None = None, served: bool = False,
            stub: dict | None = None, ctx: SimpleCtx | None = None, cache: ChunkCache | None = None,
            server_token: str = TOKEN, **kw):
    cfg = MotionConfig(**{"workers": 2, "chunk_min": 30, "chunk_max": 60, "sample_memory_seconds": 0.5,
                          **(config or {})})
    ctx = ctx or SimpleCtx()
    with serve_stage(server_token, tl.model_dump(mode="json"), config=stub) as (base, state):
        result = asyncio.run(render_motion(
            ctx, tl, tmp / "out.mp4", _settings(base, tmp), token=TOKEN, work_dir=tmp / "work",
            cache=cache, local_files=media, config=cfg,
            served=tl.model_dump(mode="json") if served else None, **kw))
    return result, state, ctx


def _frame_numbers(ffmpeg: str, path: Path, size: tuple[int, int]) -> tuple[list[np.ndarray], list[int]]:
    frames = gray_frames(ffmpeg, path, size)
    w, h = size
    return frames, [decode_bits(g, x0=0, y0=0, w=w, h=h / 4) for g in frames]


def _audio(ffmpeg: str, path: Path, tmp: Path) -> np.ndarray:
    wav = tmp / "audio.wav"
    from .fixtures import _run

    _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000",
          "-c:a", "pcm_s16le", str(wav)])
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)


@pytest.mark.container  # also in the Docker test image (Debian's ffmpeg 5.1, the pinned headless shell)
def test_landscape_render_is_frame_exact_across_chunk_joins(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([3.0, 4.0, 2.5], media_scene=1, quiz_scene=2)
    cache = ChunkCache(tmp_path / "cache")
    result, state, ctx = _render(tl, tmp_path, media, cache=cache, soft_subtitles=True)
    total = round(tl.total_duration * 30)
    assert result.frames == total == 285 and result.workers == 2 and result.slots == 2
    assert len(result.chunks) == 6 and result.reused == 0 and all(c.attempts == 1 for c in result.chunks)
    frames, numbers = _frame_numbers(ffmpeg, tmp_path / "out.mp4", (320, 180))
    assert numbers == list(range(total))  # every frame once, in order, through 6 chunk joins

    # the media frames: scene 1 starts at frame 90; source frame floor((t - 3.0) * 30) % 60
    for f in range(90, 210):
        assert decode_bits(frames[f], x0=0, y0=90, w=160, h=22.5) == (f - 90) % CLIP_FRAMES, f
    assert not any("/__frames/" in r for r in state.requests)  # served by the worker, never by the server
    assert sum(c.media_frames for c in result.chunks) >= 120

    # captions are not burned (the stage's caption quadrant stays dark); a default-on soft track instead
    assert frames[30][150:175, 170:310].mean() < 60
    qa = result.container
    assert qa.ok and qa.frames == total and qa.subtitle_tracks == 1 and qa.subtitle_default
    assert qa.video_start == 0.0 and qa.raw_video_start == 0.0 and abs(qa.av_offset or 0) < 1 / 30
    assert qa.color == {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709",
                        "color_range": "tv"}
    assert [c["frames"] for c in qa.chunks] == [c.f1 - c.f0 for c in result.chunks]

    # the classic mix: narration from 0.5 s into each scene, ticks and the ding in the quiz scene
    pcm = _audio(ffmpeg, tmp_path / "out.mp4", tmp_path)

    def rms(a: float, b: float) -> float:
        return float(np.sqrt(np.mean(pcm[int(a * 8000):int(b * 8000)] ** 2)))

    for start in (0.0, 3.0, 7.0):
        assert rms(start + 0.6, start + 1.4) > 4 * max(rms(start + 0.05, start + 0.4), 1.0)

    summary = result.summary()
    assert summary["engine"] == "motion" and len(summary["fps_per_worker"]) == 2
    assert summary["timings"]["capture"] > 0 and summary["memory_mb"]["total"] > 0
    progress = [e["progress"] for e in ctx.events if e["type"] == "progress"]
    assert progress == sorted(progress)
    assert len(list((tmp_path / "cache").glob("*.ok"))) == 6  # every chunk cached for a retry
    assert not (tmp_path / "work" / "media").exists() or not any((tmp_path / "work" / "media").iterdir())


def test_a_retry_reuses_the_cached_chunks(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([2.0, 2.0], media_scene=0)
    cache = ChunkCache(tmp_path / "cache")
    first, _, _ = _render(tl, tmp_path / "a", media, cache=cache)
    second, _, _ = _render(tl, tmp_path / "b", media, cache=cache)
    assert second.reused == len(second.chunks) == len(first.chunks) == 2
    assert _frame_numbers(ffmpeg, tmp_path / "b" / "out.mp4", (320, 180))[1] == list(range(120))
    # the caption layer is part of the key: burned captions are other pixels, nothing is reused
    third, _, _ = _render(tl, tmp_path / "c", media, cache=cache, burn_captions=True)
    assert third.reused == 0


@pytest.mark.container  # also in the Docker test image (Debian's ffmpeg 5.1, the pinned headless shell)
def test_served_timeline_portrait_and_burned_captions(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([2.0, 2.5], size=(180, 320), fmt="portrait")
    # the server refuses the token: the page can only render because the worker serves the timeline itself
    result, state, _ = _render(tl, tmp_path, media, served=True, burn_captions=True, server_token="another")
    assert not any(r.startswith("/api/render/timeline") for r in state.requests)
    frames, numbers = _frame_numbers(ffmpeg, tmp_path / "out.mp4", (180, 320))
    assert numbers == list(range(135)) and result.size == (180, 320)
    assert frames[10][250:310, 100:170].mean() > 200  # the caption layer (white quadrant) is drawn by the stage
    assert result.container.ok and result.container.subtitle_tracks == 0  # burned: no soft track


def test_page_recycling_keeps_every_frame(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([3.0, 3.0, 3.0], media_scene=1)
    result, _, _ = _render(tl, tmp_path, media, config={"workers": 1, "recycle_frames": 40})
    assert [c.f1 - c.f0 for c in result.chunks] == [45] * 6
    assert result.worker_stats[0].recycles == 5 and result.worker_stats[0].pages == 6  # a fresh page per chunk
    assert _frame_numbers(ffmpeg, tmp_path / "out.mp4", (320, 180))[1] == list(range(270))


def test_presigned_links_are_signed_again_for_every_page(ffmpeg, media, tmp_path: Path) -> None:
    """With S3 presigned URLs the job passes ``refresh_served``: every page the engine opens (each worker's first,
    every recycle) answers its timeline request with a freshly signed copy, never the one resolved at the start."""
    tl = dynamic_timeline([3.0, 3.0, 3.0], media_scene=1)
    calls: list[int] = []

    def refresh() -> dict:
        calls.append(1)
        return tl.model_dump(mode="json")

    # the server refuses the token: every page renders from the copy the worker serves
    result, state, _ = _render(tl, tmp_path, media, served=True, server_token="another", refresh_served=refresh,
                               config={"workers": 1, "recycle_frames": 40})
    assert result.worker_stats[0].pages == 6 and len(calls) == 6  # one signing per page load
    assert not any(r.startswith("/api/render/timeline") for r in state.requests)
    assert _frame_numbers(ffmpeg, tmp_path / "out.mp4", (320, 180))[1] == list(range(270))


@pytest.mark.parametrize(("mode", "error", "code"), [
    ("bad_info", StageError, "stage_contract"),
    ("bad_version", StageError, "stage_contract"),
    ("boot_error", StageError, "render_page_timeline_invalid"),
    ("bad_plan", MotionError, "stage_contract"),
    ("foreign_media", MotionError, "stage_contract"),  # a key the lecture does not name is never fetched
])
def test_contract_violations_fail_without_retry(ffmpeg, media, tmp_path: Path, mode: str, error: type,
                                                code: str) -> None:
    tl = dynamic_timeline([1.0, 1.0], media_scene=0)
    with pytest.raises(error) as ei:
        _render(tl, tmp_path, media, stub={"mode": mode}, config={"workers": 1})
    assert ei.value.code == code and not ei.value.retryable


def test_missing_clip_fails_before_capture(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([1.0], media_scene=0)
    with pytest.raises(MotionError) as ei:
        _render(tl, tmp_path, {AUDIO_KEY: media[AUDIO_KEY]})
    assert ei.value.code == "asset_missing" and not ei.value.retryable


def test_missing_narration_fails_before_capture(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([2.0, 2.0])
    ctx = SimpleCtx()
    with pytest.raises(MotionError) as ei:
        _render(tl, tmp_path, {CLIP_KEY: media[CLIP_KEY]}, ctx=ctx)
    assert ei.value.code == "asset_missing" and not ei.value.retryable
    assert not list((tmp_path / "work").glob("chunk_*.ts"))  # no frame was captured
    assert not any("Capturing" in str(e.get("message")) for e in ctx.events)


def test_a_failed_audio_master_stops_the_capture(ffmpeg, media, tmp_path: Path) -> None:
    """A narration file ffmpeg cannot read fails the scene mix within a second: the workers stop then, not after
    capturing the whole lecture."""
    bad = tmp_path / "corrupt.wav"
    bad.write_bytes(b"RIFF" + b"\x00" * 64)
    tl = dynamic_timeline([4.0, 4.0, 4.0, 4.0])
    with pytest.raises(FFmpegError):
        _render(tl, tmp_path, {AUDIO_KEY: bad}, config={"workers": 1, "chunk_min": 30, "chunk_max": 30})
    assert len(list((tmp_path / "work").glob("chunk_*.ts"))) < 16  # stopped long before the 16 chunks


def test_cancel_stops_the_capture(ffmpeg, media, tmp_path: Path) -> None:
    tl = dynamic_timeline([4.0, 4.0])

    class CancelLater(SimpleCtx):
        def progress(self, stage: str, fraction: float, message: str = "") -> None:
            super().progress(stage, fraction, message)
            if stage == "capture" and fraction > 0.2:
                self.cancelled = True

    with pytest.raises(JobCancelled):
        _render(tl, tmp_path, media, ctx=CancelLater(), config={"workers": 1})
    assert not (tmp_path / "out.mp4").exists()
    shutil.rmtree(tmp_path / "work", ignore_errors=True)  # nothing holds its files any more (no live encoder)
    assert not (tmp_path / "work").exists()
