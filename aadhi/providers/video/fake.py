"""Deterministic offline video: a 4 s ffmpeg test pattern (H.264 MP4), tinted per prompt."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path

from ...config import Settings, get_settings
from .._common import aspect_dims, emit_usage
from .._process import run_process
from ..base import ImageInput, ProviderError, Usage, UsageSink, VideoResult
from ..media import probe_media

FAKE_SECONDS = 4.0


def _even(n: int) -> int:
    return n - (n % 2)


async def render_test_video(prompt: str, width: int, height: int, seconds: float, settings: Settings) -> bytes:
    """``ffmpeg -f lavfi testsrc2`` (hue derived from the prompt) -> MP4 bytes."""
    hue = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:4], 16) % 360
    tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="aadhi-fakevideo-"))
    out = tmp / "fake.mp4"
    try:
        result = await run_process(
            [
                settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file,pipe",
                "-f", "lavfi", "-i", f"testsrc2=size={_even(width)}x{_even(height)}:rate=25",
                "-t", f"{seconds:.3f}",
                "-vf", f"hue=h={hue}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "2",
                "-movflags", "+faststart", "-an", "-f", "mp4", str(out),
            ],
            timeout=120.0,
        )
        if result.returncode != 0 or not out.exists():
            err = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
            raise ProviderError(f"fake: ffmpeg failed: {err[0][:200]}", provider="fake")
        return await asyncio.to_thread(out.read_bytes)
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, True)


class FakeVideo:
    """Offline ``VideoProvider``."""

    name = "fake"
    paid = False
    aspects = frozenset({"16:9", "9:16"})
    resumable = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    async def generate(
        self,
        prompt: str,
        *,
        aspect: str = "16:9",
        reference_image: ImageInput | None = None,
        timeout_s: int = 420,
        on_usage: UsageSink | None = None,
    ) -> VideoResult:
        """4 s 1280x720 (16:9) or 720x1280 (9:16) test pattern."""
        settings = self.settings or get_settings()
        width, height = aspect_dims(aspect if aspect in ("16:9", "9:16") else "16:9")
        data = await render_test_video(prompt or "", width, height, FAKE_SECONDS, settings)
        info = await probe_media(data, settings=settings)
        await emit_usage(on_usage, Usage(provider="fake", model="fake-video", operation="video",
                                         seconds=info.duration, units=1))
        return VideoResult(data=data, mime="video/mp4", duration=info.duration, width=info.width or width,
                           height=info.height or height)
