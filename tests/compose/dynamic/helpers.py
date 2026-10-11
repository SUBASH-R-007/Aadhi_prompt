"""Shared helpers of the Dynamic QA tests: the Dynamic fixture timelines (from ``evals/render_parity.py``), tool
checks, and a reference capture + encode with the §7.5 parameters (test-only).

The reference encoder mirrors DYNAMIC_VIDEO_DESIGN §7.5 exactly (CDP JPEG q92 -> ``image2pipe``/``mjpeg`` -> full-range
BT.601 to TV BT.709 -> libx264 High, closed GOP of 15, ``-bf 2`` -> MPEG-TS chunks; the concat demuxer with
``-c copy``; the final mux with AAC and ``-movflags +faststart+negative_cts_offsets``). It stands in for the motion
engine where the engine is not the subject (the container checks, the parity eval's own test, the chunk-join
continuity check); the engine's own tests use ``aadhi.compose.motion``.
"""

from __future__ import annotations

import functools
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import pytest

from aadhi.compose.capabilities import resolve_tool
from aadhi.compose.ffmpeg import subprocess_env
from aadhi.config import ROOT_DIR, Settings

FPS = 30
CHUNK_VF = ("scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")


def chunk_encode_args(*, preset: str = "veryfast", crf: int = 17) -> list[str]:
    """The §7.5 chunk encoder (every chunk identical, so every chunk starts with an IDR frame)."""
    return ["-c:v", "libx264", "-profile:v", "high", "-preset", preset, "-crf", str(crf), "-maxrate", "12M",
            "-bufsize", "24M", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", "-bf", "2",
            "-x264-params", "open-gop=0", "-an", "-f", "mpegts"]


@functools.cache
def parity_eval() -> Any:
    """``evals/render_parity.py`` loaded by path (``evals`` is not a package)."""
    spec = importlib.util.spec_from_file_location("aadhi_eval_render_parity_dyn", ROOT_DIR / "evals" / "render_parity.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@functools.cache
def video_qa() -> Any:
    spec = importlib.util.spec_from_file_location("aadhi_eval_video_qa_dyn", ROOT_DIR / "evals" / "video_qa.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def settings() -> Settings:
    return Settings(_env_file=None)


def dynamic_timeline(fmt: str = "landscape") -> Any:
    return parity_eval().dynamic_timeline(fmt, settings=settings())


# Set by the Docker ``test`` image (Dockerfile, target ``test``), which runs the B1 suites in the blocking CI job: there
# every tool is part of the image, so a missing one is a broken image, not a developer machine without it.
CONTAINER_TESTS_ENV = "AADHI_CONTAINER_TESTS"


def container_tests() -> bool:
    """True inside the Docker ``test`` image (``AADHI_CONTAINER_TESTS`` set to anything but empty, 0, false, no)."""
    return os.environ.get(CONTAINER_TESTS_ENV, "").strip().lower() not in ("", "0", "false", "no")


def missing(reason: str) -> NoReturn:
    """Skip the test for a missing tool (``reason``), or fail it inside the Docker ``test`` image (see
    :func:`container_tests`): a CI job that skipped the determinism, parity or engine suites would pass green."""
    if container_tests():
        pytest.fail(f"{reason}, but the Docker test image ({CONTAINER_TESTS_ENV}=1) must provide it: the suite would "
                    "have been skipped", pytrace=False)
    pytest.skip(reason)


def tools() -> tuple[str, str]:
    """``(ffmpeg, ffprobe)``, else skip (fail in the Docker test image: :func:`missing`)."""
    s = settings()
    ffmpeg, ffprobe = resolve_tool(s.ffmpeg_path), resolve_tool(s.ffprobe_path)
    if ffmpeg is None or ffprobe is None:
        missing("ffmpeg/ffprobe are not installed")
    return ffmpeg, ffprobe


@functools.cache
def _chromium() -> bool | None:
    from aadhi.compose.capabilities import chromium_installed

    return chromium_installed()


def _playwright_installed() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def need_chromium() -> None:
    """Playwright and its Chromium, else skip (fail in the Docker test image: :func:`missing`)."""
    if not _playwright_installed():
        missing("playwright is not installed")
    if _chromium() is False:
        missing("Playwright's Chromium is not installed")


def run(cmd: Sequence[str], *, data: bytes | None = None, cwd: Path | None = None) -> bytes:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(list(cmd), input=data, capture_output=True, timeout=600, env=subprocess_env(), cwd=cwd,
                          check=False, **kwargs)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    return proc.stdout


def ffmpeg(*args: str, data: bytes | None = None, cwd: Path | None = None) -> None:
    stdin = [] if data is not None else ["-nostdin"]
    run([tools()[0], "-hide_banner", *stdin, "-y", "-loglevel", "error", *args], data=data, cwd=cwd)


def probe(path: Path, *args: str) -> dict[str, Any]:
    return json.loads(run([tools()[1], "-v", "error", *args, "-of", "json", str(path)]).decode("utf-8") or "{}")


def encode_chunk(jpegs: Iterable[bytes], out: Path, *, fps: int = FPS, preset: str = "veryfast", crf: int = 17,
                 vf: str = CHUNK_VF) -> Path:
    """JPEG frames -> one MPEG-TS chunk with the §7.5 settings (``vf``: another filter chain, for broken files)."""
    ffmpeg("-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(fps), "-i", "pipe:0", "-vf", vf,
           *chunk_encode_args(preset=preset, crf=crf), str(out), data=b"".join(jpegs))
    return out


def join_and_mux(chunks: Sequence[Path], out: Path, *, frames: int, fps: int = FPS, audio: bool = True,
                 negative_cts: bool = True, audio_delay: float = 0.0, extra: Sequence[str] = ()) -> Path:
    """Concat the chunks (``-c copy``) and mux them with a sine master track into ``out`` (the §7.5 final mux;
    ``negative_cts=False`` and ``audio_delay`` build broken files for the remedy tests)."""
    work = out.parent
    (work / f"{out.stem}.ffconcat").write_text(
        "ffconcat version 1.0\n" + "".join(f"file {c.name}\n" for c in chunks), encoding="utf-8")
    args = ["-f", "concat", "-safe", "1", "-i", f"{out.stem}.ffconcat"]
    if audio:
        seconds = frames / fps
        master = work / f"{out.stem}.master.wav"
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}", "-ac", "2",
               "-c:a", "pcm_s16le", str(master))
        if audio_delay:
            args += ["-itsoffset", f"{audio_delay:.4f}"]
        args += ["-i", master.name, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        args += ["-map", "0:v"]
    flags = "+faststart+negative_cts_offsets" if negative_cts else "+faststart"
    ffmpeg(*args, "-c:v", "copy", *extra, "-movflags", flags, out.name, cwd=work)
    return out


def jpeg_frames(n: int, *, start: int = 0, size: tuple[int, int] = (320, 180)) -> list[bytes]:
    """``n`` full-range JPEGs with motion and a frame-number barcode (see :func:`read_barcode`)."""
    from PIL import Image, ImageDraw

    out = []
    w, h = size
    for i in range(start, start + n):
        im = Image.new("RGB", (w, h), (20, 40 + (i * 3) % 120, 90))
        draw = ImageDraw.Draw(im)
        x = (i * 7) % (w - 40)
        draw.rectangle((x, 60, x + 40, 120), fill=(240, 200, 30))
        draw_barcode(draw, i, w)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92)
        out.append(buf.getvalue())
    return out


BARCODE_BITS = 12
BARCODE_CELL = 16


def draw_barcode(draw: Any, value: int, width: int) -> None:
    """The frame number as 12 black/white cells along the top edge (bit 0 at the left)."""
    for bit in range(BARCODE_BITS):
        on = (value >> bit) & 1
        x0 = bit * BARCODE_CELL
        draw.rectangle((x0, 0, x0 + BARCODE_CELL - 1, BARCODE_CELL - 1), fill=(255, 255, 255) if on else (0, 0, 0))


def read_barcode(gray: bytes, width: int) -> int:
    """The number :func:`draw_barcode` drew, from a full-range gray frame of ``width`` pixels per row."""
    value = 0
    mid = BARCODE_CELL // 2
    for bit in range(BARCODE_BITS):
        x = bit * BARCODE_CELL + mid
        if gray[mid * width + x] > 128:
            value |= 1 << bit
    return value


def decoded_numbers(path: Path, *, width: int, height: int) -> list[int]:
    """The barcode of every decoded frame of ``path`` (display order)."""
    raw = run([tools()[0], "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
               "-vf", "scale=in_range=tv:out_range=pc,format=gray", "-f", "rawvideo", "-"])
    size = width * height
    return [read_barcode(raw[i:i + size], width) for i in range(0, len(raw) - size + 1, size)]


async def reference_render(base: str, timeline: Any, out: Path, *, token: str = "reference.render",
                           chunk_frames: int = 120, shift: int = 0) -> list[Path]:
    """Capture every frame of ``timeline`` from the (stub) stage at ``base`` (CDP JPEG q92) and encode it with the §7.5
    chunk encoder into ``out`` (chunks of ``chunk_frames``, sine audio). ``shift`` captures frame ``n + shift`` as
    frame ``n`` (a deliberately wrong render for the negative tests). Returns the chunk files."""
    import asyncio

    from aadhi.compose.dynamic.stage_page import open_stage
    from aadhi.compose.qa import expected_frame_count

    total = expected_frame_count(timeline.total_duration, timeline.fps)
    jpegs: list[bytes] = []
    async with open_stage(settings(), size=(timeline.width, timeline.height), token=token, base_url=base,
                          served_timeline=timeline.model_dump(mode="json")) as page:
        await page.set_layers(captions=bool(timeline.presentation.captions_burn), stage=True)
        for n in range(total):
            await page.seek(min(total - 1, n + shift))
            jpegs.append(await page.capture_jpeg())
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for k, start in enumerate(range(0, total, chunk_frames)):
        path = out.parent / f"{out.stem}_chunk_{k:04d}.ts"
        await asyncio.to_thread(encode_chunk, jpegs[start:start + chunk_frames], path)
        chunks.append(path)
    await asyncio.to_thread(join_and_mux, chunks, out, frames=total)
    return chunks
