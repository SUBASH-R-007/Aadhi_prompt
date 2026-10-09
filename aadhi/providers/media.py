"""Media inspection with ffprobe (async, never blocks the event loop)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from ._process import run_process
from .base import ProviderError

_SNIFF = (
    (b"RIFF", ".wav"),
    (b"ID3", ".mp3"),
    (b"\xff\xfb", ".mp3"),
    (b"\xff\xf3", ".mp3"),
    (b"\xff\xf2", ".mp3"),
    (b"OggS", ".ogg"),
    (b"fLaC", ".flac"),
    (b"\x1aE\xdf\xa3", ".webm"),
    (b"GIF8", ".gif"),
    (b"\x89PNG", ".png"),
)


@dataclass
class MediaInfo:
    duration: float
    width: int | None = None
    height: int | None = None
    format_name: str = ""
    has_video: bool = False
    has_audio: bool = False
    sample_rate: int | None = None


def sniff_extension(data: bytes) -> str:
    """Best-effort file extension for ``data`` (helps ffprobe pick a demuxer)."""
    head = data[:16]
    for magic, ext in _SNIFF:
        if head.startswith(magic):
            return ext
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return ".mp4"
    return ".bin"


def _write_temp(data: bytes, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="aadhi-probe-", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return Path(name)


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _parse_probe(raw: bytes) -> MediaInfo:
    try:
        info = json.loads(raw.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderError("ffprobe returned invalid JSON", provider="local") from exc
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    durations: list[float] = []
    for value in [fmt.get("duration")] + [s.get("duration") for s in streams]:
        try:
            if value not in (None, "N/A"):
                durations.append(float(value))
        except (TypeError, ValueError):
            continue
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not durations:
        raise ProviderError("ffprobe could not determine the media duration", provider="local")
    sample_rate = None
    if audio and audio.get("sample_rate"):
        with contextlib.suppress(TypeError, ValueError):
            sample_rate = int(audio["sample_rate"])
    return MediaInfo(
        duration=float(fmt["duration"]) if fmt.get("duration") not in (None, "N/A") else max(durations),
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        format_name=str(fmt.get("format_name") or ""),
        has_video=video is not None,
        has_audio=audio is not None,
        sample_rate=sample_rate,
    )


async def probe_media(source: bytes | Path | str, *, settings: Settings | None = None, timeout: float = 60.0) -> MediaInfo:
    """Probe duration/dimensions with ffprobe (``-protocol_whitelist file,pipe``).

    ``bytes`` are written to a temporary file first: probing a pipe cannot seek, so ffprobe
    would not report a duration for MP3/MP4 streams.
    """
    settings = settings or get_settings()
    tmp: Path | None = None
    if isinstance(source, (bytes, bytearray)):
        if not source:
            raise ProviderError("cannot probe empty media", provider="local")
        tmp = await asyncio.to_thread(_write_temp, bytes(source), sniff_extension(bytes(source)))
        path = tmp
    else:
        path = Path(source)
    try:
        result = await run_process(
            [
                settings.ffprobe_path,
                "-v", "error",
                "-protocol_whitelist", "file,pipe",
                "-show_entries", "format=duration,format_name:stream=codec_type,width,height,duration,sample_rate",
                "-of", "json",
                str(path),
            ],
            timeout=timeout,
        )
    finally:
        if tmp is not None:
            await asyncio.to_thread(_unlink, tmp)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise ProviderError(f"ffprobe failed: {settings.redact(err[0])[:200]}", provider="local")
    return _parse_probe(result.stdout)
