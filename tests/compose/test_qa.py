"""Post-render output QA: pure parsers/statistics, and real measurements of small encodes (slow)."""

from __future__ import annotations

import asyncio
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from aadhi.compose.ffmpeg import run_ffmpeg
from aadhi.compose.qa import (
    RenderQA,
    _grid_step,
    black_strips,
    expected_frame_count,
    grid_stats,
    inspect_render,
    parse_rate,
    parse_volumedetect,
)
from aadhi.config import Settings


def test_parse_rate() -> None:
    assert parse_rate("30/1") == 30.0 and parse_rate("30000/1001") == pytest.approx(29.97, abs=1e-3)
    assert parse_rate("0/0") is None and parse_rate(None) is None and parse_rate("x/y") is None


def test_parse_volumedetect() -> None:
    text = ("[Parsed_volumedetect_0 @ 0x1] n_samples: 96000\n[Parsed_volumedetect_0 @ 0x1] mean_volume: -23.4 dB\n"
            "[Parsed_volumedetect_0 @ 0x1] max_volume: -3.0 dB\n")
    assert parse_volumedetect(text) == (-23.4, -3.0)
    mean, peak = parse_volumedetect("mean_volume: -inf dB\nmax_volume: -inf dB")
    assert mean == -math.inf and peak == -math.inf
    assert parse_volumedetect("nothing") == (None, None)


def test_grid_stats() -> None:
    assert grid_stats([0, 512, 1024, 1536], 512) == (True, 0, 0)
    assert grid_stats([1024, 0, 512], 512) == (True, 0, 0)  # unordered packets (B-frames)
    assert grid_stats([100, 612, 1124], 512) == (True, 0, 0)  # relative to the first pts
    assert grid_stats([0, 512, 1030, 1536], 512) == (False, 1, 2)
    assert grid_stats([0, 512, 1536], 512) == (False, 0, 1)  # a missing frame
    assert grid_stats([], 512) == (True, 0, 0)


def test_grid_step() -> None:
    assert _grid_step("1/15360", 30) == 512 and _grid_step("1/12800", 25) == 512
    assert _grid_step("1/1000", 30) is None and _grid_step("bad", 30) is None and _grid_step("1/90000", 30) == 3000


def test_black_strips() -> None:
    w, h = 40, 20
    frame = np.full((h, w), 120, dtype=np.uint8)
    assert black_strips(frame.tobytes(), w, h) == []
    frame[:, :2] = 0
    frame[-2:, :] = 3
    assert black_strips(frame.tobytes(), w, h) == ["left", "bottom"]
    assert black_strips(b"\x00" * 10, w, h) == []  # short buffer


def test_expected_frames_and_summary() -> None:
    assert expected_frame_count(4.366667, 30) == 131 and expected_frame_count(0, 30) == 0
    qa = RenderQA(expected_frames=10, fps=30, frames=10, declared_cfr=True, on_grid=True)
    s = qa.summary()
    assert s["ok"] and s["constant_frame_rate"] and s["frames"] == 10 and s["problems"] == []
    qa.problems.append("x")
    assert not qa.ok and qa.summary()["ok"] is False


# --- real files (ffmpeg) --------------------------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def _encode(path: Path, settings: Settings, *, frames: int, tone: bool = True, pad: bool = False,
            bt709: bool = True) -> Path:
    vf = "pad=iw:ih+80:0:40:black," if pad else ""
    audio = (["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={frames / 30}"] if tone else
             ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={frames / 30}"])
    tags = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={frames / 30}", *audio,
                            "-vf", f"{vf}format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                            "-video_track_timescale", "15360", *(tags if bt709 else []), "-c:a", "aac",
                            "-shortest", str(path)], settings=settings))
    return path


@pytest.mark.slow
def test_inspect_a_good_render(tmp_path: Path, settings: Settings) -> None:
    mp4 = _encode(tmp_path / "ok.mp4", settings, frames=45)
    qa = asyncio.run(inspect_render(mp4, settings=settings, fps=30, expected_frames=45, expect_audio=True,
                                    expect_bt709=True))
    assert qa.ok, qa.problems
    assert qa.frames == 45 and qa.declared_cfr and qa.on_grid is True
    assert qa.has_audio and qa.audible and qa.audio_max_db > -30
    assert qa.black_edges == [] and qa.color["color_space"] == "bt709"
    assert qa.warnings == []


@pytest.mark.slow
def test_inspect_flags_problems(tmp_path: Path, settings: Settings) -> None:
    silent = _encode(tmp_path / "silent.mp4", settings, frames=30, tone=False, pad=True, bt709=False)
    qa = asyncio.run(inspect_render(silent, settings=settings, fps=30, expected_frames=60, expect_audio=True,
                                    expect_bt709=True, expect_subtitles=True))
    assert not qa.ok
    assert any("30 frames" in p for p in qa.problems)
    assert "the narration is silent in the video" in qa.problems and qa.audible is False
    assert qa.black_edges == ["bottom", "top"]
    assert any("not tagged BT.709" in w for w in qa.warnings)
    assert any("caption track" in w for w in qa.warnings)
    # a lecture without narration: silence is fine
    calm = asyncio.run(inspect_render(silent, settings=settings, fps=30, expected_frames=30, expect_audio=False))
    assert calm.ok, calm.problems


@pytest.mark.slow
def test_inspect_video_without_sound(tmp_path: Path, settings: Settings) -> None:
    mp4 = tmp_path / "mute.mp4"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=1", "-c:v", "libx264", "-preset",
                            "ultrafast", str(mp4)], settings=settings))
    qa = asyncio.run(inspect_render(mp4, settings=settings, fps=30, expected_frames=30, expect_audio=True))
    assert "the video has no sound track" in qa.problems and not qa.has_audio
    wav = tmp_path / "x.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    no_video = asyncio.run(inspect_render(wav, settings=settings, fps=30, expected_frames=3, expect_audio=False))
    assert no_video.problems == ["the video has no picture stream"]
