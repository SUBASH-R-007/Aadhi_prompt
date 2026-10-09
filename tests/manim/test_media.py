"""ffmpeg/ffprobe helpers: probe, exact-duration conform (pad + trim), frame extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aadhi.manim import media
from tests.manim.fakes import make_test_video

pytestmark = pytest.mark.slow


def frame_count(path: Path) -> int:
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return int(out.stdout.strip())


@pytest.fixture()
def clip(tmp_path: Path) -> Path:
    return make_test_video(tmp_path / "clip.mp4", 2.0, fps=15)


async def test_probe(clip: Path, app_env) -> None:
    info = await media.probe(clip, app_env)
    assert info.duration == pytest.approx(2.0, abs=0.07)
    assert (info.width, info.height, info.fps) == (320, 180, 15.0)


@pytest.mark.parametrize("total", [3.4, 1.2, 2.0, 0.5])
async def test_conform_pads_or_trims_to_the_exact_duration(clip: Path, tmp_path: Path, app_env, total: float) -> None:
    out = tmp_path / f"out-{total}.mp4"
    info = await media.conform(clip, out, total, app_env)
    # MP4 durations are frame-granular: the result is within half a frame of the requested length.
    assert abs(info.duration - total) <= 0.5 / 15 + 1e-6
    assert frame_count(out) == round(total * 15)
    assert (info.width, info.height) == (320, 180)


async def test_conform_freezes_the_last_frame(clip: Path, tmp_path: Path, app_env) -> None:
    out = tmp_path / "padded.mp4"
    await media.conform(clip, out, 4.0, app_env)
    last_source = (await media.extract_frames(clip, [1.9], app_env))[0]
    late = await media.extract_frames(out, [2.5, 3.9], app_env)
    from io import BytesIO

    from PIL import Image, ImageChops

    def img(data: bytes):
        return Image.open(BytesIO(data)).convert("RGB")

    def max_diff(a: bytes, b: bytes) -> int:
        return max(hi for _lo, hi in ImageChops.difference(img(a), img(b)).getextrema())

    # testsrc changes every frame (counter digits differ by ~255); only encoding noise remains when frozen.
    assert max_diff(late[0], late[1]) < 40, "padding repeats one frame"
    assert max_diff(last_source, late[1]) < 40, "the frozen frame is the source's last frame"
    moving = await media.extract_frames(clip, [0.5, 1.5], app_env)
    assert max_diff(moving[0], moving[1]) > 100, "sanity: the source itself is animated"


async def test_extract_frames(clip: Path, app_env) -> None:
    frames = await media.extract_frames(clip, [0.0, 1.0, 1.99, 7.5], app_env, max_width=160)
    assert len(frames) == 4 and all(f.startswith(b"\x89PNG") for f in frames)
    from io import BytesIO

    from PIL import Image

    assert Image.open(BytesIO(frames[0])).size == (160, 90)


async def test_errors_are_media_errors(tmp_path: Path, app_env) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(media.MediaError):
        await media.probe(junk, app_env)
    with pytest.raises(media.MediaError):
        await media.conform(junk, tmp_path / "x.mp4", 1.0, app_env)
    missing = app_env.model_copy(update={"ffprobe_path": str(tmp_path / "no-ffprobe")})
    with pytest.raises(media.MediaError, match="not installed"):
        await media.probe(junk, missing)
