"""ffmpeg feature checks for the motion engine and the Dynamic audio chain (DYNAMIC_VIDEO_DESIGN §2.8, §7.5, §7.9).

The Docker ``test`` image runs these on Debian bookworm's ffmpeg 5.1 (``pytest -m container``); everywhere else
they run against the local ffmpeg (``FFMPEG_PATH``) with the slow suite. Set ``AADHI_EXPECT_FFMPEG`` (e.g.
``5.1``) to also pin the version: the image does, so a base-image change that moves ffmpeg is noticed.

Checked: ``image2pipe`` + ``mjpeg`` input with the explicit full-range BT.601 -> BT.709 TV conversion and
``setparams``; the chunk encoder's settings (High profile, closed GOP of 15, ``-bf 2``, ``-x264-params``) into
MPEG-TS; concatenating MPEG-TS chunks with the concat demuxer and ``-c copy`` frame-exactly; the final remux with
``-movflags +faststart+negative_cts_offsets`` starting video and audio at 0 (probed with ``-ignore_editlist 1``, next
to a control mux without the flag: a decoded start of 0 also comes from the ``elst`` the muxer writes by default);
``loudnorm``, ``ebur128`` and ``alimiter``; ``zscale`` (libzimg) tone-mapping an HDR clip to BT.709.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from aadhi.compose.capabilities import resolve_tool
from aadhi.compose.ffmpeg import parse_ffmpeg_version, subprocess_env
from aadhi.compose.loudness import LoudnessTarget, ebur128, limiter_filter, loudnorm_measure
from aadhi.config import Settings

pytestmark = [pytest.mark.slow, pytest.mark.container]

FPS = 30
W, H = 320, 180
EXPECT_ENV = "AADHI_EXPECT_FFMPEG"

# The motion engine's chunk encoder (§7.5), with the default preset and CRF.
CHUNK_VF = ("scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
CHUNK_ENCODE = ["-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "17", "-maxrate", "12M",
                "-bufsize", "24M", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", "-bf", "2",
                "-x264-params", "open-gop=0", "-an", "-f", "mpegts"]


@pytest.fixture(scope="module")
def tools() -> tuple[str, str]:
    settings = Settings(_env_file=None)
    ffmpeg, ffprobe = resolve_tool(settings.ffmpeg_path), resolve_tool(settings.ffprobe_path)
    if ffmpeg is None or ffprobe is None:
        if os.environ.get(EXPECT_ENV):
            pytest.fail("ffmpeg/ffprobe are missing from the test image")
        pytest.skip("ffmpeg/ffprobe are not installed")
    return ffmpeg, ffprobe


def _run(cmd: list[str], *, data: bytes | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, input=data, capture_output=True, timeout=300, env=subprocess_env(), cwd=cwd,
                          check=False, **kwargs)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    return proc


def _ffmpeg(tools: tuple[str, str], *args: str, data: bytes | None = None, cwd: Path | None = None) -> None:
    stdin = [] if data is not None else ["-nostdin"]  # piped frames arrive on stdin
    _run([tools[0], "-hide_banner", *stdin, "-y", "-loglevel", "error", *args], data=data, cwd=cwd)


def _probe(tools: tuple[str, str], path: Path, *args: str) -> dict[str, Any]:
    out = _run([tools[1], "-v", "error", *args, "-of", "json", str(path)]).stdout
    return json.loads(out.decode("utf-8") or "{}")


def _jpeg_frames(n: int, start: int = 0) -> bytes:
    """``n`` full-range sRGB JPEGs (what Chrome's CDP capture produces) with motion, so B-frames are used."""
    out = io.BytesIO()
    for i in range(start, start + n):
        im = Image.new("RGB", (W, H), (20, 40 + (i * 3) % 120, 90))
        draw = ImageDraw.Draw(im)
        x = (i * 7) % (W - 40)
        draw.rectangle((x, 60, x + 40, 120), fill=(240, 200, 30))
        draw.ellipse((W - x - 30, 20, W - x, 50), fill=(200, 30, 60))
        im.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _chunk(tools: tuple[str, str], path: Path, frames: int, start: int = 0) -> Path:
    _ffmpeg(tools, "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(FPS), "-i", "pipe:0", "-vf", CHUNK_VF,
            *CHUNK_ENCODE, str(path), data=_jpeg_frames(frames, start))
    return path


def _video_stream(tools: tuple[str, str], path: Path, entries: str) -> dict[str, Any]:
    data = _probe(tools, path, "-count_packets", "-count_frames", "-select_streams", "v:0", "-show_entries",
                  f"stream={entries},nb_read_packets,nb_read_frames")
    return data["streams"][0]


# --- version and components ------------------------------------------------------------------------


def test_expected_ffmpeg_version(tools: tuple[str, str]) -> None:
    expected = os.environ.get(EXPECT_ENV, "").strip()
    if not expected:
        pytest.skip(f"{EXPECT_ENV} is not set (only the Docker test image pins the ffmpeg version)")
    version = parse_ffmpeg_version(_run([tools[0], "-hide_banner", "-version"]).stdout.decode("utf-8", "replace"))
    assert version is not None and f"{version[0]}.{version[1]}" == expected, version


def _listing(tools: tuple[str, str], flag: str) -> set[str]:
    text = _run([tools[0], "-hide_banner", flag]).stdout.decode("utf-8", "replace")
    names: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.update(parts[1].split(","))  # muxer/demuxer rows can list several names
    return names


def test_required_components_are_built_in(tools: tuple[str, str]) -> None:
    filters = _listing(tools, "-filters")
    missing = {"setparams", "scale", "format", "loudnorm", "ebur128", "alimiter", "aresample", "zscale", "tonemap",
               "volume", "amix", "afade", "overlay"} - filters
    assert not missing, f"ffmpeg lacks the filters {sorted(missing)}"
    assert {"image2pipe", "concat", "mpegts", "mov"} <= _listing(tools, "-demuxers")
    assert {"mpegts", "mp4", "null"} <= _listing(tools, "-muxers")
    assert {"mjpeg", "h264", "aac"} <= _listing(tools, "-decoders")
    assert {"libx264", "aac", "mjpeg"} <= _listing(tools, "-encoders")


# --- motion chunks ---------------------------------------------------------------------------------


def test_mjpeg_pipe_chunk_has_the_motion_encoder_settings(tools: tuple[str, str], tmp_path: Path) -> None:
    chunk = _chunk(tools, tmp_path / "chunk.ts", 45)
    s = _video_stream(tools, chunk, "codec_name,profile,pix_fmt,width,height,color_range,color_space,"
                                    "color_primaries,color_transfer,has_b_frames,r_frame_rate")
    assert int(s["nb_read_packets"]) == 45 and int(s["nb_read_frames"]) == 45
    assert (s["codec_name"], s["profile"], s["pix_fmt"]) == ("h264", "High", "yuv420p")
    assert (s["width"], s["height"], s["r_frame_rate"]) == (W, H, f"{FPS}/1")
    assert (s["color_range"], s["color_space"], s["color_primaries"], s["color_transfer"]) == (
        "tv", "bt709", "bt709", "bt709")
    assert int(s["has_b_frames"]) >= 1
    frames = _probe(tools, chunk, "-select_streams", "v:0", "-show_entries", "frame=key_frame,pict_type")["frames"]
    types = [f["pict_type"] for f in frames]
    assert "B" in types
    keys = [i for i, f in enumerate(frames) if int(f["key_frame"]) == 1]
    assert keys == [0, 15, 30]  # closed GOP of 15: every chunk can start with an IDR frame
    # the full-range BT.601 JPEG colours survive the explicit conversion (the yellow box: R high, B low)
    raw = _run([tools[0], "-hide_banner", "-loglevel", "error", "-i", str(chunk), "-frames:v", "1", "-vf",
                "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24", "-f", "rawvideo", "-"]).stdout
    r, g, b = raw[(90 * W + 20) * 3: (90 * W + 20) * 3 + 3]
    assert r > 200 and g > 160 and b < 80, (r, g, b)


def test_mpegts_chunks_concatenate_frame_exactly(tools: tuple[str, str], tmp_path: Path) -> None:
    sizes = [45, 60, 37]
    start = 0
    for n, size in enumerate(sizes):
        _chunk(tools, tmp_path / f"chunk_{n:04d}.ts", size, start)
        start += size
    (tmp_path / "chunks.ffconcat").write_text(
        "ffconcat version 1.0\n" + "".join(f"file chunk_{n:04d}.ts\n" for n in range(len(sizes))), encoding="utf-8")
    _ffmpeg(tools, "-f", "concat", "-safe", "1", "-i", "chunks.ffconcat", "-c", "copy", "joined.ts", cwd=tmp_path)
    s = _video_stream(tools, tmp_path / "joined.ts", "codec_name")
    assert int(s["nb_read_packets"]) == sum(sizes) and int(s["nb_read_frames"]) == sum(sizes)
    frames = _probe(tools, tmp_path / "joined.ts", "-select_streams", "v:0", "-show_entries",
                    "frame=pts_time")["frames"]
    times = [float(f["pts_time"]) for f in frames]
    steps = {round((b - a) * FPS, 3) for a, b in zip(times, times[1:], strict=False)}
    assert steps == {1.0}  # no gap, duplicate or reordering at the chunk joins


def test_negative_cts_offsets_start_video_and_audio_at_zero(tools: tuple[str, str], tmp_path: Path) -> None:
    """The final remux puts the first shown frame at 0 through ``negative_cts_offsets`` itself, not only through the
    edit list the mov muxer writes by default: the raw timestamps (``-ignore_editlist 1``, what a player that ignores
    edit lists shows) start at 0, and the same mux without the flag (the control) starts 2 frames late (``-bf 2``)."""
    sizes = [45, 60]
    start = 0
    for n, size in enumerate(sizes):
        _chunk(tools, tmp_path / f"chunk_{n:04d}.ts", size, start)
        start += size
    (tmp_path / "chunks.ffconcat").write_text(
        "ffconcat version 1.0\nfile chunk_0000.ts\nfile chunk_0001.ts\n", encoding="utf-8")
    seconds = sum(sizes) / FPS
    _ffmpeg(tools, "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-ac", "2",
            "-c:a", "pcm_s16le", "master.wav", cwd=tmp_path)

    def mux(name: str, *movflags: str) -> Path:
        _ffmpeg(tools, "-f", "concat", "-safe", "1", "-i", "chunks.ffconcat", "-i", "master.wav", "-map", "0:v",
                "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "384k", "-ar", "48000", *movflags, name,
                cwd=tmp_path)
        return tmp_path / name

    def raw_first_pts(path: Path) -> float:  # what a player that ignores edit lists shows first
        frames = _probe(tools, path, "-ignore_editlist", "1", "-select_streams", "v:0", "-show_entries",
                        "frame=pts_time", "-read_intervals", "%+#5")["frames"]
        return float(frames[0]["pts_time"])

    out = mux("out.mp4", "-movflags", "+faststart+negative_cts_offsets")
    streams = {s["codec_type"]: s for s in _probe(tools, out, "-show_entries",
                                                  "stream=codec_type,start_time,nb_frames")["streams"]}
    video, audio = float(streams["video"]["start_time"]), float(streams["audio"]["start_time"])
    assert video == pytest.approx(0.0, abs=1e-6), streams
    assert abs(video - audio) < 1 / FPS, streams
    assert int(streams["video"]["nb_frames"]) == sum(sizes)
    first = _probe(tools, out, "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                   "-read_intervals", "%+#5")["frames"][0]
    assert float(first["pts_time"]) == pytest.approx(0.0, abs=1e-6)  # the first shown frame is at 0
    # the flag, not the default elst, puts the video at 0
    raw = {s["codec_type"]: s for s in _probe(tools, out, "-ignore_editlist", "1", "-show_entries",
                                              "stream=codec_type,start_time")["streams"]}
    assert float(raw["video"]["start_time"]) == pytest.approx(0.0, abs=1e-6), raw
    assert abs(float(raw["video"]["start_time"]) - float(raw["audio"]["start_time"])) < 1 / FPS, raw
    assert raw_first_pts(out) == pytest.approx(0.0, abs=1e-6)
    # negative control: the same mux without the flag starts at 0 only through its edit list
    control = mux("control.mp4", "-movflags", "+faststart")
    assert raw_first_pts(control) > 0.5 / FPS  # 2 frames (0.0667 s) late with -bf 2


# --- audio chain -----------------------------------------------------------------------------------


def test_loudnorm_ebur128_and_alimiter(tools: tuple[str, str], tmp_path: Path) -> None:
    settings = Settings(_env_file=None, ffmpeg_path=tools[0])
    tone = tmp_path / "tone.wav"
    # a stereo 1 kHz sine at 0.1 (-20 dBFS) measures -20 LUFS
    _ffmpeg(tools, "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=4", "-af",
            "pan=stereo|c0=c0|c1=c0,volume=0.8", "-c:a", "pcm_s16le", str(tone))
    summary = asyncio.run(ebur128(tone, settings))
    assert summary.integrated == pytest.approx(-20.0, abs=0.3) and summary.true_peak == pytest.approx(-20.0, abs=0.5)
    first = asyncio.run(loudnorm_measure(tone, settings, LoudnessTarget(-16.0, -1.5, 9.0)))
    assert first.input_i == pytest.approx(-20.0, abs=0.3) and first.normalization_type in ("dynamic", "linear")
    loud = tmp_path / "limited.wav"
    _ffmpeg(tools, "-i", str(tone), "-af", f"volume=24dB,{limiter_filter()},aresample=48000", "-c:a", "pcm_s16le",
            str(loud))
    peak = asyncio.run(ebur128(loud, settings)).sample_peak
    assert peak <= 20 * math.log10(0.84) + 0.3  # +4 dBFS before the limiter, held at about -1.5 dBFS


# --- HDR clips ---------------------------------------------------------------------------------------


def test_zscale_tonemaps_an_hdr_clip_to_bt709(tools: tuple[str, str], tmp_path: Path) -> None:
    hdr = tmp_path / "hdr.mp4"
    _ffmpeg(tools, "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=0.5", "-vf",
            "format=yuv420p10le,setparams=range=tv:color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", str(hdr))
    src = _video_stream(tools, hdr, "pix_fmt,color_transfer,color_primaries")
    assert (src["color_transfer"], src["color_primaries"]) == ("smpte2084", "bt2020")
    sdr = tmp_path / "sdr.mp4"
    _ffmpeg(tools, "-i", str(hdr), "-vf",
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,"
            "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-c:v", "libx264", "-preset", "ultrafast", str(sdr))
    out = _video_stream(tools, sdr, "pix_fmt,color_range,color_space,color_transfer,color_primaries")
    assert (out["pix_fmt"], out["color_range"], out["color_space"], out["color_transfer"],
            out["color_primaries"]) == ("yuv420p", "tv", "bt709", "bt709", "bt709")
    assert int(out["nb_read_frames"]) == 15
